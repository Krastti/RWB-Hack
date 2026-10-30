"""Offline contract validation; does not execute agents or generated code.

Usage: python contracts/agents/validate.py --check
       python contracts/agents/validate.py agent-result path/to/result.json
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from urllib.parse import urljoin

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
except ImportError:
    sys.exit("Install dependencies: python -m pip install -r contracts/agents/requirements.txt")

ROOT = Path(__file__).resolve().parent
PAYLOAD_SCHEMAS = {
    "business_requirements": "analysis-input", "requirements": "requirements",
    "questions": "questions", "architecture": "architecture",
    "generation_plan": "generation-plan", "manifest": "manifest", "traceability": "traceability",
}


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def loads(value):
    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, "Duplicate JSON property")
            result[key] = item
        return result

    def constant(_):
        raise ContractError("Non-finite JSON number")

    def floating(value):
        number = float(value)
        require(math.isfinite(number), "Non-finite JSON number")
        return number

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)


def load(path):
    return loads(path.read_text(encoding="utf-8"))


def walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def unique(items, key, label):
    ids = [item[key] for item in items]
    require(len(ids) == len(set(ids)), f"Duplicate {label}")


def acyclic(items, key, dependency_key):
    graph = {item[key]: item[dependency_key] for item in items}
    require(all(set(deps) <= graph.keys() for deps in graph.values()), "Unknown dependency")
    active, done = set(), set()

    def visit(node):
        require(node not in active, "Dependency cycle")
        if node in done:
            return
        active.add(node)
        for child in graph[node]:
            visit(child)
        active.remove(node)
        done.add(node)

    for node in graph:
        visit(node)


def validate_semantics(name, data):
    if isinstance(data, dict):
        for field, key in [("questions", "question_id"), ("answers", "question_id"),
                           ("assumptions", "id")]:
            if field in data:
                unique(data[field], key, field)
    if name == "requirements":
        ids = [item["id"] for group in data.values() if isinstance(group, list)
               for item in group if "id" in item]
        require(len(ids) == len(set(ids)), "Duplicate analysis identifier")
        frs = {item["id"] for item in data["functional_requirements"]}
        roles = {item["id"] for item in data["roles"]}
        for item in walk(data):
            if isinstance(item, dict) and "related_fr_ids" in item:
                require(set(item["related_fr_ids"]) <= frs, "Unknown related FR")
        for item in data["scenarios"]:
            require(set(item["role_ids"]) <= roles, "Unknown scenario role")
        covered = {fr for item in data["acceptance_criteria"] for fr in item["related_fr_ids"]}
        require(covered == frs, "Every FR must have an acceptance criterion")
        questions = {q["question_id"]: q for q in data["questions"]}
        for item in walk(data):
            if isinstance(item, dict) and item.get("source") == "answer" and "quote" in item:
                question = questions.get(item["question_id"], {})
                require(item["quote"] in question.get("answer", ""), "Evidence refers to an absent answer")
    elif name == "agent-result":
        unique(data["output_artifacts"], "artifact_id", "artifact ID")
        unique(data["output_artifacts"], "path", "artifact path")
        usage = data["usage"]
        unique(usage["model_calls"], "call_id", "model call ID")
        require(usage["calls"] == len(usage["model_calls"]), "Usage calls disagree with model_calls")
        for field in ("input_tokens", "output_tokens"):
            values = [call[field] for call in usage["model_calls"]]
            expected = None if None in values else sum(values)
            require(usage[field] == expected, "Usage token aggregate mismatch")
    elif name == "architecture":
        for field in ("components", "decisions", "integrations"):
            unique(data[field], "id", field)
        acyclic(data["components"], "id", "depends_on")
    elif name == "generation-plan":
        unique(data["tasks"], "task_id", "generation task ID")
        unique(data["tasks"], "stage_id", "generation stage ID")
        acyclic(data["tasks"], "task_id", "depends_on")
        owners = []
        for task in data["tasks"]:
            for kind in ("output_paths", "output_directories"):
                for path in task[kind]:
                    for other, owner, is_directory in owners:
                        if owner != task["task_id"]:
                            overlaps = (path == other or is_directory and path.startswith(other + "/")
                                        or kind == "output_directories" and other.startswith(path + "/"))
                            require(not overlaps, "Conflicting generation output owners")
                    owners.append((path, task["task_id"], kind == "output_directories"))
    elif name == "manifest":
        unique(data["artifacts"], "artifact_id", "manifest artifact ID")
        unique(data["artifacts"], "path", "manifest path")
        require(all(a["path"] != "manifest.json" for a in data["artifacts"]),
                "Manifest cannot include its own checksum")
        if data["traceability_artifact_id"] is not None:
            require(any(a["artifact_id"] == data["traceability_artifact_id"] and a["kind"] == "traceability"
                        for a in data["artifacts"]), "Missing traceability artifact")
    elif name == "traceability":
        unique(data["coverage"], "fr_id", "traceability FR")


class Contracts:
    def __init__(self):
        self.schemas = {p.name.removesuffix(".schema.json"): load(p)
                        for p in sorted((ROOT / "v1").glob("*.schema.json"))}
        require(bool(self.schemas), "No local schemas")
        ids = [schema["$id"] for schema in self.schemas.values()]
        require(len(ids) == len(set(ids)), "Duplicate schema $id")
        self.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in self.schemas.values())
        for schema in self.schemas.values():
            Draft202012Validator.check_schema(schema)
            for item in walk(schema):
                if isinstance(item, dict) and "$ref" in item:
                    # Every reference is resolved against the in-memory registry. No HTTP retrieval.
                    self.registry.resolver(base_uri=schema["$id"]).lookup(urljoin(schema["$id"], item["$ref"]))

    def validate(self, name, data):
        require(name in self.schemas and name != "common", "Unknown public schema")
        validator = Draft202012Validator(self.schemas[name], registry=self.registry,
                                        format_checker=FormatChecker())
        error = next(validator.iter_errors(data), None)
        if error:
            pointer = "/" + "/".join(str(part) for part in error.absolute_path)
            # No raw values or provider bodies in diagnostics.
            raise ContractError(f"{name}: schema violation at {pointer} ({error.validator})")
        validate_semantics(name, data)
        if name == "artifact":
            self.artifact(data)
        elif name == "agent-result":
            for artifact in data["output_artifacts"]:
                self.artifact(artifact)

    def artifact(self, artifact):
        if "content" not in artifact:
            return  # Resolving owned storage_ref and verifying its bytes belongs to storage adapter.
        raw = artifact["content"].encode("utf-8")
        require(artifact["size_bytes"] == len(raw), "Inline artifact size mismatch")
        require(artifact["checksum"] == hashlib.sha256(raw).hexdigest(), "Inline artifact checksum mismatch")
        if artifact["kind"] in PAYLOAD_SCHEMAS:
            require(artifact["media_type"] == "application/json", "Structured artifact requires application/json")
            self.validate(PAYLOAD_SCHEMAS[artifact["kind"]], loads(artifact["content"]))
        elif artifact["media_type"] == "application/json":
            loads(artifact["content"])


def example_file(filename):
    path = (ROOT / "examples" / filename).resolve()
    require(path.is_relative_to((ROOT / "examples").resolve()), "Example escapes examples directory")
    return path


def check_bundle(contracts, bundle):
    request = load(example_file(bundle["input"]))
    result = load(example_file(bundle["result"]))
    for field in ("run_id", "stage_id", "attempt", "contract_version"):
        require(request[field] == result[field], "Input/result context mismatch")
    resolved = {key: load(example_file(file)) for key, file in bundle["resolved_inputs"].items()}
    require(set(request["input_artifact_ids"]) == resolved.keys(), "Unresolved example input artifact")
    outputs = {a["artifact_id"]: a for a in result["output_artifacts"]}
    require(not (outputs.keys() & resolved.keys()), "Output reuses immutable input artifact ID")
    available = resolved.keys() | outputs.keys()
    if result["status"] in {"succeeded", "waiting_for_input"}:
        require(result["usage"]["calls"] <= request["limits"]["max_calls"], "Example exceeds call budget")
        reported = result["usage"]["output_tokens"]
        require(reported is None or reported <= request["limits"]["max_output_tokens"], "Example exceeds token budget")
    pending = [q for q in result["questions"] if q["required"] and "answer" not in q]
    require(len(pending) <= request["limits"].get("max_critical_questions", 3), "Too many critical questions")
    source = next((value for value in resolved.values() if "requirements_text" in value), None)
    reqs = None
    for artifact in outputs.values():
        if artifact["kind"] == "requirements" and "content" in artifact:
            reqs = loads(artifact["content"])
    if reqs is not None:
        require(reqs["questions"] == result["questions"], "Questions disagree between result and requirements")
        require(reqs["assumptions"] == result["assumptions"], "Assumptions disagree between result and requirements")
        require(source is not None, "Missing business input for analysis example")
        previous = resolved.get(source["previous_requirements_artifact_id"])
        known = {q["question_id"]: q for q in previous["questions"]} if previous else {}
        answers = {key: q["answer"] for key, q in known.items() if "answer" in q}
        for answer in source["answers"]:
            require(answer["question_id"] in known, "Answer refers to unknown previous question")
            answers[answer["question_id"]] = answer["value"]
        for question in reqs["questions"]:
            require(question.get("answer") == answers.get(question["question_id"]), "Invented question answer")
        for item in walk(reqs):
            if isinstance(item, dict) and set(item) == {"source", "quote", "question_id"}:
                original = source["requirements_text"] if item["source"] == "requirements" else answers.get(item["question_id"], "")
                require(item["quote"] in original, "Evidence is absent from business input")
        if previous:
            require(source["questions_revision"] == previous["questions_revision"], "Stale question revision")
            for field in ("functional_requirements", "nonfunctional_requirements", "roles"):
                require({x["id"] for x in previous[field]} <= {x["id"] for x in reqs[field]}, "Previous IDs removed")
            require(set(known) <= {q["question_id"] for q in reqs["questions"]}, "Previous question ID removed")
        for artifact in outputs.values():
            if artifact["kind"] == "questions" and "content" in artifact:
                questions = loads(artifact["content"])
                require(questions["questions"] == reqs["questions"], "questions.json disagrees with requirements")
                require(questions["questions_revision"] == reqs["questions_revision"], "Question revisions disagree")
    for artifact in outputs.values():
        if "content" not in artifact or artifact["media_type"] != "application/json":
            continue
        value = loads(artifact["content"])
        kind = artifact["kind"]
        if kind in {"architecture", "traceability"}:
            require(value["requirements_artifact_id"] in available, "Missing requirements artifact")
        if kind == "architecture":
            for contract_kind, reference in value["contracts"].items():
                require(reference["artifact_id"] in outputs, "Architecture contract reference is unresolved")
                actual = outputs[reference["artifact_id"]]
                require(all(actual[key] == item for key, item in reference.items()), "Architecture metadata mismatch")
                require(actual["kind"] == contract_kind, "Wrong architecture contract kind")
            for integration in value["integrations"]:
                require(integration["contract_artifact_id"] in available, "Unresolved integration contract")
            require(value["contract_version"] == request["contract_version"], "Architecture contract mismatch")
            require(value["profile_id"] == request["profile_id"], "Architecture profile mismatch")
        if kind == "generation_plan":
            require(value["contract_version"] == request["contract_version"], "Plan contract mismatch")
            require(value["profile_id"] == request["profile_id"], "Plan profile mismatch")
            for task in value["tasks"]:
                require(set(task["input_artifact_ids"]) <= available, "Generation input reference is unresolved")
    return 1


def check_snapshot(snapshot):
    manifest = load(example_file(snapshot["manifest"]))
    traceability = load(example_file(snapshot["traceability"]))
    requirements = load(example_file(snapshot["requirements"]))
    produced = []
    for file in snapshot["results"]:
        result = load(example_file(file))
        require(result["run_id"] == manifest["run_id"], "Snapshot run mismatch")
        require(result["contract_version"] == manifest["contract_version"], "Snapshot contract mismatch")
        produced.extend(result["output_artifacts"])
    produced.extend(load(example_file(file)) for file in snapshot["additional_artifacts"])
    unique(produced, "artifact_id", "snapshot artifact ID")
    actual = {a["artifact_id"]: a for a in produced}
    require(set(actual) == {a["artifact_id"] for a in manifest["artifacts"]}, "Incomplete snapshot inventory")
    for reference in manifest["artifacts"]:
        require(all(actual[reference["artifact_id"]][key] == value for key, value in reference.items()),
                "Snapshot artifact metadata mismatch")
    frs = {fr["id"] for fr in requirements["functional_requirements"]}
    require(frs == {row["fr_id"] for row in traceability["coverage"]}, "Incomplete FR coverage inventory")
    reference = actual.get(traceability["requirements_artifact_id"])
    require(reference is not None and reference["kind"] == "requirements", "Unresolved traceability source")
    require(loads(reference["content"]) == requirements, "Traceability source content mismatch")
    trace = actual[manifest["traceability_artifact_id"]]
    require(loads(trace["content"]) == traceability, "Snapshot traceability content mismatch")
    for artifact in produced:
        require(set(artifact["related_fr_ids"]) <= frs, "Snapshot references unknown FR")
    for row in traceability["coverage"]:
        require(set(row["artifact_ids"]) <= actual.keys(), "Traceability references unknown artifact")


def check_examples(contracts):
    index = load(ROOT / "examples" / "index.json")
    listed = set()
    for entry in index["documents"]:
        require(entry["file"] not in listed, "Duplicate example index entry")
        listed.add(entry["file"])
        contracts.validate(entry["schema"], load(example_file(entry["file"])))
    actual = {p.name for p in (ROOT / "examples").glob("*.json") if p.name != "index.json"}
    require(listed == actual, "Example index is incomplete")
    for bundle in index["bundles"]:
        check_bundle(contracts, bundle)
    for snapshot in index["snapshots"]:
        check_snapshot(snapshot)
    return len(listed), len(index["bundles"]), len(index["snapshots"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate C-02 schemas and examples without network access")
    parser.add_argument("--check", action="store_true", help="Check all local schemas, examples and linked bundles")
    parser.add_argument("schema", nargs="?", help="Schema name, e.g. agent-input or requirements")
    parser.add_argument("file", type=Path, nargs="?")
    args = parser.parse_args(argv)
    if args.check and (args.schema or args.file) or not args.check and not (args.schema and args.file):
        parser.error("Use --check OR a schema name and JSON file")
    try:
        contracts = Contracts()
        if args.check:
            documents, bundles, snapshots = check_examples(contracts)
            print(f"OK: {len(contracts.schemas)} schemas, {documents} examples, "
                  f"{bundles} linked bundles, {snapshots} snapshot; offline")
        else:
            contracts.validate(args.schema, load(args.file))
            print(f"OK: {args.schema}")
        return 0
    except Exception as exc:
        # Library errors can contain input values; expose only our deliberately safe messages.
        message = str(exc) if isinstance(exc, ContractError) else type(exc).__name__
        print("INVALID: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
