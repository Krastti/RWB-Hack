"""Deterministic task ownership and a planned tree; no model-selected dispatch."""

from pathlib import PurePosixPath

from contracts.agents.validate import ContractError, validate_profile_grants


OWNERS = ("frontend", "backend", "migrations", "integrations", "documentation", "assembly", "verification", "android")


def validate_required_artifacts(profile):
    """Reject inconsistent ownership, including inactive conditional files."""
    plan = {"profile_id": profile["profile_id"], "tasks": [
        {"owner_agent": item["owner"], "output_paths": [item["path"]],
         "output_directories": []}
        for item in profile["required_artifacts"]
    ]}
    try:
        validate_profile_grants(plan, profile)
    except ContractError:
        raise ContractError("ARCHITECTURE_REQUIRED_ARTIFACT_NOT_ALLOWED") from None


def make_plan(context, requirements_id, requirements, profile, design):
    owners = [owner for owner in OWNERS if owner in profile["ownership"]["agents"]
              and owner != "android" and (owner != "integrations" or design["integrations"])]
    dependencies = {
        "frontend": [], "backend": [], "migrations": ["backend"], "verification": ["backend", "frontend"],
        "integrations": ["backend"],
        "documentation": [o for o in owners if o not in {"documentation", "assembly"}],
        "assembly": [o for o in owners if o != "assembly"],
    }
    fr_ids = sorted(f["id"] for f in requirements["functional_requirements"])
    tasks = []
    for owner in owners:
        grant = profile["ownership"]["agents"][owner]
        tasks.append({
            "task_id": owner, "stage_id": owner, "owner_agent": owner,
            "depends_on": dependencies[owner], "input_artifact_ids": [requirements_id],
            "output_paths": list(grant.get("paths", [])),
            "output_directories": list(grant.get("directories", [])),
            "related_fr_ids": list(fr_ids),
        })
    plan = {"schema_version": "1.0", "profile_id": context["profile_id"],
            "contract_version": context["contract_version"], "tasks": tasks}
    validate_profile_grants(plan, profile)
    return plan


def make_tree(profile, design):
    has_integrations = bool(design["integrations"])
    files = {
        item["path"]: item["owner"] for item in profile["required_artifacts"]
        if item["condition"] == "always" or item["condition"] == "has_integrations" and has_integrations
    }
    directories = set()

    def add_parents(path):
        directories.update(str(p) for p in PurePosixPath(path).parents if str(p) != ".")

    for path in files:
        add_parents(path)
    if has_integrations:
        for condition in profile["conditional_directories"]:
            directories.update(condition["directories"])
    # Design directories include contracts; coding tasks keep their own grants.
    grants = [directory for owner in ("architecture", *OWNERS) if owner in profile["ownership"]["agents"] and owner != "android" and (owner != "integrations" or has_integrations)
              for directory in profile["ownership"]["agents"][owner].get("directories", [])]
    for component in design["components"]:
        for path in component["output_directories"]:
            if path not in directories and not any(path == g or path.startswith(g + "/") for g in grants):
                raise ContractError("ARCHITECTURE_COMPONENT_PATH_NOT_ALLOWED")
            directories.add(path)
            add_parents(path)
    for path in tuple(directories):
        add_parents(path)
    all_paths = [*files, *directories]
    if any(len(path) > profile["limits"]["artifacts"]["max_path_length"] for path in all_paths):
        raise ContractError("ARCHITECTURE_PATH_TOO_LONG")
    if files.keys() & directories or any(parent in files for path in all_paths
            for parent in (str(p) for p in PurePosixPath(path).parents)):
        raise ContractError("ARCHITECTURE_FILE_DIRECTORY_CONFLICT")
    if len({p.casefold() for p in all_paths}) != len(all_paths):
        raise ContractError("ARCHITECTURE_PATH_COLLISION")
    if len(files) > profile["limits"]["artifacts"]["max_files"]:
        raise ContractError("ARCHITECTURE_FILE_BUDGET_EXCEEDED")
    return sorted(
        [{"path": p, "type": "file", "owner_agent": owner} for p, owner in files.items()]
        + [{"path": p, "type": "directory"} for p in directories], key=lambda entry: entry["path"],
    )
