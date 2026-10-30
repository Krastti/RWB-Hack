"""Серверные лимиты и снимок разрешённого профиля запуска."""
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..services.run_queries.dto import Limits


class RunCreationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limits: Limits

    @classmethod
    def from_env(cls, *, profile: dict[str, Any] | None = None) -> "RunCreationConfig":
        defaults = (profile if profile is not None else load_run_profile())["limits"]["run"]
        return cls(limits=Limits(
            max_calls=int(os.environ.get("RUN_MAX_CALLS", defaults["max_calls"])),
            max_output_tokens=int(os.environ.get("RUN_MAX_OUTPUT_TOKENS", defaults["max_output_tokens"])),
        ))


def load_run_profile() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "templates/profiles/web-fastapi-angular/profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    if (profile["profile_id"] != "web-fastapi-angular" or not profile["profile_version"]
            or not profile["contracts"]["agent_schema_version"]):
        raise ValueError("Invalid run profile")
    return profile
