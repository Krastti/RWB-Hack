"""CLI: schemas, validation and explicit real LLM analysis. No Git side effects."""

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .agent import RequirementsAnalyst, validate_analysis
from .llm import OpenAICompatibleClient, load_json
from .models import AgentInput, AgentResult, Analysis


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Requirements Analyst Agent")
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema", help="Print the local draft JSON Schema")
    schema.add_argument("kind", choices=["input", "analysis", "result"])
    validate = commands.add_parser("validate", help="Validate a saved analysis without LLM calls")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--analysis", type=Path, required=True)
    analyze = commands.add_parser("analyze", help="Analyze through the configured real model")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--response-format", choices=["json_schema", "json_object"], default="json_schema")
    args = parser.parse_args(argv)
    if args.command == "schema":
        model = {"input": AgentInput, "analysis": Analysis, "result": AgentResult}[args.kind]
        print(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2))
        return 0
    try:
        raw = args.input.read_text(encoding="utf-8")
        load_json(raw)  # Reject duplicate keys before Pydantic's JSON-mode UUID parsing.
        request = AgentInput.model_validate_json(raw)
        if args.command == "validate":
            analysis = Analysis.model_validate(load_json(args.analysis.read_text(encoding="utf-8")))
            validated = validate_analysis(analysis, request)
            pending = [q.id for q in validated.questions if q.critical and q.answer is None]
            print(json.dumps({"valid": True, "decision": "wait" if pending else "allow",
                              "blocked_question_ids": pending}, ensure_ascii=False))
            return 0
        base_url = os.environ.get("LLM_BASE_URL", "")
        model_name = os.environ.get("LLM_MODEL", "")
        if not base_url or not model_name:
            print(json.dumps({"error": {"code": "LLM_CONFIGURATION",
                "message": "Задайте LLM_BASE_URL и LLM_MODEL; ключ — через LLM_API_KEY."}}, ensure_ascii=False), file=sys.stderr)
            return 2
        client = OpenAICompatibleClient(base_url=base_url, model=model_name,
            api_key=os.environ.get("LLM_API_KEY", ""), response_format=args.response_format)
        result = RequirementsAnalyst(client).run(request)
        # The caller/storage adapter owns writes; model paths never become filesystem paths.
        print(result.model_dump_json(indent=2))
        return 1 if result.status == "failed" else 0
    except (OSError, ValueError, ValidationError, RecursionError):
        print(json.dumps({"error": {"code": "INVALID_INPUT_OR_CONFIGURATION",
            "message": "Проверьте файл, локальную схему и конфигурацию. Данные ошибки скрыты."}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
