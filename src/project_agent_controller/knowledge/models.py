from __future__ import annotations

from datetime import date
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromptStatus(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"


class LessonStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    PROJECT_APPROVED = "PROJECT_APPROVED"
    SHARED_CANDIDATE = "SHARED_CANDIDATE"
    SHARED_APPROVED = "SHARED_APPROVED"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"


class PromptMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_type: Literal["prompt"]
    prompt_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    status: PromptStatus
    required_inputs: tuple[str, ...] = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    body: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = ""
    summary: str = ""

    @model_validator(mode="after")
    def validate_body_hash(self) -> PromptMetadata:
        actual = sha256(self.body.encode("utf-8")).hexdigest()
        if actual != self.content_sha256:
            raise ValueError("content_sha256 does not match prompt body")
        return self

    @property
    def entry_id(self) -> str:
        return self.prompt_id


class KnownProblem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_type: Literal["known_problem"]
    problem_id: str = Field(min_length=3, max_length=128)
    project_id: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1)
    technologies: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    fingerprints: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = Field(min_length=1)
    status: Literal["confirmed", "deprecated", "revoked"] = "confirmed"

    @property
    def entry_id(self) -> str:
        return self.problem_id


class Applicability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    technologies: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()

    @property
    def has_predicate(self) -> bool:
        return any((self.technologies, self.components, self.workflows, self.risk_tags))


class Lesson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_type: Literal["lesson"]
    lesson_id: str = Field(min_length=3, max_length=128)
    scope: Literal["project", "shared"]
    project_id: str | None = None
    status: LessonStatus
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1)
    applicability: Applicability
    verification_refs: tuple[str, ...] = Field(min_length=1)
    counterexamples: tuple[str, ...] = Field(min_length=1)
    review_after: date
    generated_by_ai: bool
    fingerprints: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> Lesson:
        if self.scope == "project":
            if not self.project_id:
                raise ValueError("project lesson requires project_id")
            if self.status not in {LessonStatus.PROJECT_APPROVED, LessonStatus.DEPRECATED}:
                raise ValueError("project lesson must be PROJECT_APPROVED or DEPRECATED")
        else:
            if self.project_id is not None:
                raise ValueError("shared lesson must not set project_id")
            if self.status is not LessonStatus.SHARED_APPROVED:
                raise ValueError("shared lesson must be SHARED_APPROVED")
            if not self.applicability.has_predicate:
                raise ValueError("shared lesson requires machine-readable applicability")
        return self

    @property
    def entry_id(self) -> str:
        return self.lesson_id


KnowledgeEntry = PromptMetadata | KnownProblem | Lesson
