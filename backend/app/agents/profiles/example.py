"""Offline A-29 example: python -m backend.app.agents.profiles.example."""
import json

from . import ProfileLoader


def main():
    loader = ProfileLoader()
    profile = loader.load("web-fastapi-angular")
    saved = profile.snapshot()
    restored = loader.restore(saved, expected_checksum=profile.checksum,
                              expected_rules_checksum=profile.metadata["rules_checksum"])
    assert restored.allows("backend", "backend/app/services/orders.py")
    assert not restored.allows("backend", "backend/migrations/versions/0001.py")
    assert not restored.allows("documentation", "docs/system-analysis.md")
    print(json.dumps({"profile": restored.metadata,
                      "frontend_outputs": [a["path"] for a in restored.required_outputs("frontend")],
                      "limits": restored.limits("frontend")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
