
"""Declarative acceptance requirements for a skill revision."""
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from guizhi.domain.capabilities import Hash
from guizhi.domain.platform import RunId
from guizhi.domain.skills import Slug, Strict

Scalar = str | float | int | bool


class ImprovementRequest(Strict):
    run_id: RunId
    quality: str
    quality_sha256: Hash


class Fixture(Strict):
    file: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")]
    sha256: Hash
    layer: str | None = None


class Check(Strict):
    step: Slug
    statistic: Slug
    equals: Scalar


class Scenario(Strict):
    id: Slug
    materials: dict[Slug, list[Fixture]] = Field(max_length=12)
    parameters: dict[Slug, Any]
    status: Literal["succeeded", "failed"]
    assertions: list[Check] = Field(default_factory=list, max_length=16)
    failure_code: Slug | None = None

    @model_validator(mode="after")
    def expected_outcome(self):
        if (self.status == "succeeded") != bool(self.assertions) or (
                self.status == "failed") != bool(self.failure_code):
            raise ValueError("Successful cases require assertions; failed cases require a failure code")
        if sum(map(len, self.materials.values())) > 32:
            raise ValueError("Too many frozen materials")
        return self


class SkillQuality(Strict):
    kind: Literal["workflow", "parameter"]
    skill: Slug
    primary: Slug
    basis: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    cases: list[Scenario] = Field(min_length=2, max_length=8)
    correction: dict[Slug, Any] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def check_matrix(self):
        ids = {case.id for case in self.cases}
        primary = next((case for case in self.cases if case.id == self.primary), None)
        if (len(ids) != len(self.cases) or primary is None or
                {case.status for case in self.cases} != {"succeeded", "failed"}):
            raise ValueError("Quality requires a primary case, positive and negative cases, and unique IDs")
        parameter = self.kind == "parameter"
        if parameter != bool(self.correction) or parameter != (primary.status == "succeeded"):
            raise ValueError("The primary case must prove the claimed correction")
        return self


class WorkflowProposal(Strict):
    order: list[Slug] = Field(min_length=2, max_length=12)


class ParameterProposal(Strict):
    changes: dict[Slug, Any] = Field(min_length=1, max_length=16)
