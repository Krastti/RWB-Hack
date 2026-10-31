"""Create immutable C-02 references only after the complete bundle agrees."""

from copy import deepcopy
from hashlib import sha256
from pathlib import PurePosixPath

from jsonschema import Draft202012Validator

from contracts.agents.validate import Contracts, loads, validate_profile_grants

from .inputs import reference
from .models import OUTPUTS, canonical, require
from .naming import validate_names


def make_artifact(kind, payload, inputs, reservation):
    content = canonical(payload) + "\n"
    slot = reservation["artifacts"][kind]
    artifact = {
        "artifact_id": slot["artifact_id"], "attempt_id": reservation["attempt_id"],
        "path": OUTPUTS[kind], "kind": kind, "media_type": "application/json",
        "version": slot["version"], "schema_version": "1.0", "author_agent": "architecture",
        "checksum": sha256(content.encode("utf-8")).hexdigest(), "size_bytes": len(content.encode("utf-8")),
        "related_fr_ids": sorted(fr["id"] for fr in inputs["requirements"]["functional_requirements"]), "content": content,
    }
    Contracts().validate("artifact", artifact)
    return artifact


def validate_artifact(artifact, kind, inputs, reservation):
    contracts = Contracts()
    contracts.validate("artifact", artifact)
    slot = reservation["artifacts"][kind]
    require(artifact["artifact_id"] == slot["artifact_id"]
            and artifact["attempt_id"] == reservation["attempt_id"] and artifact["version"] == slot["version"]
            and artifact["kind"] == kind and artifact["path"] == OUTPUTS[kind]
            and artifact["author_agent"] == "architecture" and "content" in artifact
            and set(artifact["related_fr_ids"]) == {fr["id"] for fr in inputs["requirements"]["functional_requirements"]},
            "ARCHITECTURE_OUTPUT_IDENTITY_MISMATCH")
    require(artifact["size_bytes"] <= inputs["profile"]["limits"]["artifacts"]["max_file_bytes"],
            "ARCHITECTURE_FILE_BUDGET_EXCEEDED")
    if kind != "openapi":
        require(artifact["media_type"] == "application/json", "ARCHITECTURE_OUTPUT_MEDIA_TYPE")
        payload = loads(artifact["content"])
        require(payload["contract_version"] == inputs["context"]["contract_version"]
                and payload["profile_id"] == inputs["context"]["profile_id"], "ARCHITECTURE_CONTRACT_VERSION_MISMATCH")
        if kind == "data_model":
            require(payload["requirements_artifact_id"] == inputs["requirements_artifact"]["artifact_id"],
                    "ARCHITECTURE_SOURCE_MISMATCH")
        if kind == "ui_spec":
            require(payload["profile_checksum"] == inputs["profile_checksum"], "ARCHITECTURE_PROFILE_CHECKSUM_MISMATCH")
        return payload


def validate_proposal(proposal, inputs):
    context = inputs["context"]
    require(proposal["draft_version"] == "a05/1.0" and proposal["ready_for_generation"] is False,
            "ARCHITECTURE_INVALID_PROPOSAL")
    for key in ("run_id", "stage_id", "attempt", "contract_version", "profile_id"):
        require(proposal[key] == context[key], "ARCHITECTURE_PROPOSAL_CONTEXT_MISMATCH")
    for key in ("profile_checksum", "rules_checksum"):
        require(proposal[key] == inputs[key], "ARCHITECTURE_PROPOSAL_SNAPSHOT_MISMATCH")
    require(proposal["profile_version"] == inputs["profile"]["profile_version"]
            and proposal["requirements_checksum"] == inputs["requirements_artifact"]["checksum"],
            "ARCHITECTURE_PROPOSAL_SNAPSHOT_MISMATCH")
    core, plan = proposal["architecture_core"], proposal["generation_plan"]
    require("contracts" not in core and core["requirements_artifact_id"] == inputs["requirements_artifact"]["artifact_id"],
            "ARCHITECTURE_INVALID_PROPOSAL")
    for value in (core, plan):
        require(value["contract_version"] == context["contract_version"] and value["profile_id"] == context["profile_id"],
                "ARCHITECTURE_CONTRACT_VERSION_MISMATCH")
    frs = {fr["id"] for fr in inputs["requirements"]["functional_requirements"]}
    require({fr for component in core["components"] for fr in component["related_fr_ids"]} == frs,
            "ARCHITECTURE_PROPOSAL_FR_MISMATCH")
    Contracts().validate("generation-plan", plan)
    validate_profile_grants(plan, inputs["profile"])
    allowed = set(context["input_artifact_ids"])
    require(all(set(task["input_artifact_ids"]) <= allowed for task in plan["tasks"]),
            "ARCHITECTURE_UNRESOLVED_PLAN_INPUT")
    tree = proposal["project_tree"]
    require(isinstance(tree, list) and bool(tree), "ARCHITECTURE_INVALID_PROJECT_TREE")
    path_validator = Draft202012Validator(Contracts().schemas["common"]["$defs"]["relative_path"])
    paths, files, tasks = set(), {}, {}
    for entry in tree:
        path, kind = entry["path"], entry["type"]
        require(path_validator.is_valid(path) and kind in {"file", "directory"}
                and path.casefold() not in paths, "ARCHITECTURE_INVALID_PROJECT_TREE")
        paths.add(path.casefold())
        if kind == "file":
            owner = entry["owner_agent"]
            files[path] = owner
            tasks.setdefault(owner, {"owner_agent": owner, "output_paths": [], "output_directories": []})["output_paths"].append(path)
    validate_profile_grants({"profile_id": context["profile_id"], "tasks": list(tasks.values())}, inputs["profile"])
    already_produced = {"requirements.json", "questions.json", "docs/system-analysis.md"}
    already_produced.update(OUTPUTS.values())
    for path, owner in files.items():
        if (owner == "analysis" and path in already_produced) or (owner == "architecture" and path in OUTPUTS.values()):
            continue
        require(any(task["owner_agent"] == owner and
                    (path in task["output_paths"] or any(
                        path.startswith(directory.rstrip("/") + "/")
                        for directory in task["output_directories"]))
                    for task in plan["tasks"]), "ARCHITECTURE_TREE_PLAN_MISMATCH")
    file_paths = {p.casefold() for p in files}
    require(not any(str(parent) in file_paths for path in paths for parent in PurePosixPath(path).parents),
            "ARCHITECTURE_INVALID_PROJECT_TREE")
    required = {item["path"]: item["owner"] for item in inputs["profile"]["required_artifacts"]
                if item["condition"] == "always" or bool(core["integrations"])}
    require(all(files.get(path) == owner for path, owner in required.items()), "ARCHITECTURE_INVALID_PROJECT_TREE")


def freeze_contracts(proposal, artifacts, inputs, reservation, document):
    """No writes: the worker accepts all five artifacts in one transaction."""
    contracts = Contracts()
    model = validate_artifact(artifacts["data_model"], "data_model", inputs, reservation)
    ui = validate_artifact(artifacts["ui_spec"], "ui_spec", inputs, reservation)
    require(document["info"]["version"] == model["contract_version"] == ui["contract_version"]
            == inputs["context"]["contract_version"], "ARCHITECTURE_CONTRACT_VERSION_MISMATCH")
    for name, artifact in (("requirements", inputs["requirements_artifact"]), ("openapi", artifacts["openapi"])):
        require(ui["sources"][name] == {key: artifact[key] for key in ("artifact_id", "checksum")},
                "ARCHITECTURE_UI_SOURCE_MISMATCH")
    validate_names(document, model, inputs["naming_policy"])
    plan = deepcopy(proposal["generation_plan"])
    contract_ids = [reservation["artifacts"][kind]["artifact_id"] for kind in ("architecture", "openapi", "data_model", "ui_spec")]
    for task in plan["tasks"]:
        task["input_artifact_ids"] = sorted(set(task["input_artifact_ids"]) | set(contract_ids)
                                                   | {inputs["requirements_artifact"]["artifact_id"]})
    contracts.validate("generation-plan", plan)
    validate_profile_grants(plan, inputs["profile"])
    artifacts = deepcopy(artifacts)
    artifacts["generation_plan"] = make_artifact("generation_plan", plan, inputs, reservation)
    core = deepcopy(proposal["architecture_core"])
    core["contracts"] = {kind: reference(artifacts[kind]) for kind in ("openapi", "data_model", "ui_spec", "generation_plan")}
    available = set(inputs["context"]["input_artifact_ids"]) | {slot["artifact_id"] for slot in reservation["artifacts"].values()}
    require(all(i["contract_artifact_id"] in available for i in core["integrations"]),
            "ARCHITECTURE_UNRESOLVED_INTEGRATION")
    next_id = max((int(d["id"].removeprefix("ADR-")) for d in core["decisions"]), default=0) + 1
    provenance = {"profile_checksum": inputs["profile_checksum"], "rules_checksum": inputs["rules_checksum"],
                  "naming_policy_checksum": sha256(canonical(inputs["naming_policy"]).encode()).hexdigest(),
                  "api_policy_checksum": sha256(canonical(inputs["api_policy"]).encode()).hexdigest(),
                  "contracts": core["contracts"]}
    core["decisions"].append({"id": f"ADR-{next_id:03}",
        "decision": "A-09 зафиксировал согласованный набор API/data/UI и план одной contract_version.",
        "reason": canonical(provenance),
        "related_fr_ids": sorted(fr["id"] for fr in inputs["requirements"]["functional_requirements"])})
    contracts.validate("architecture", core)
    artifacts["architecture"] = make_artifact("architecture", core, inputs, reservation)
    limits = inputs["profile"]["limits"]["artifacts"]
    require(len(artifacts) <= limits["max_files"] and sum(a["size_bytes"] for a in artifacts.values()) <= limits["max_total_bytes"],
            "ARCHITECTURE_ARTIFACT_BUDGET_EXCEEDED")
    for kind, artifact in artifacts.items():
        validate_artifact(artifact, kind, inputs, reservation)
    return [artifacts[kind] for kind in OUTPUTS]
