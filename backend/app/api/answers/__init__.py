"""C-01 2.1.0 answerQuestions endpoint."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import current_user
from ..common import ContractRoute
from ...services.run_control.models import Run


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: UUID
    value: str = Field(min_length=1, max_length=10000, strict=True)

    @field_validator("value")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Answer must contain a non-whitespace character")
        return value


class Answers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions_revision: int = Field(ge=1, strict=True)
    answers: list[Answer] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self):
        ids = [a.question_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question UUID")
        return self


def configure_answers(app, service):
    router = APIRouter(prefix="/api/v1/runs", tags=["answers"], route_class=ContractRoute)

    @router.post("/{run_id}/answers", status_code=202, response_model=Run,
                 response_model_exclude_none=True, operation_id="answerQuestions")
    def accept(run_id: UUID, body: Answers, request: Request,
               key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128,
                                          pattern=r"^[A-Za-z0-9._:-]+$")],
               user=Depends(current_user)):
        return service.accept(owner_id=user.id, run_id=run_id, key=key, body=body)

    app.state.answers_service = service
    app.include_router(router)
