"""Skill templates are declarative data. The narrative portion is descriptive only."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,39}$")]
Title = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(Strict):
    id: Slug
    label: Title
    kind: Literal["vector", "raster", "cad", "document"] = "vector"
    geometry: Literal["any", "point", "line", "polygon"] = "any"
    coordinate: Literal["any", "defined", "projected_meters"] = "defined"
    minimum: Annotated[int, Field(ge=0, le=32)] = 1
    maximum: Annotated[int, Field(ge=1, le=32)] = 1


class Step(Strict):
    id: Slug
    operation: str
    inputs: list[str] = Field(default_factory=list, max_length=32,
                              description='List of input references, for example ["material.project"] or ["step.check"].')
    parameters: dict[str, Any] = Field(default_factory=dict)


class Rule(Strict):
    step: Slug
    statistic: Slug
    compare: Literal["eq", "ge", "le"]
    value: int | float | str | bool


class Evidence(Strict):
    step: Slug
    kind: Slug


class Skill(Strict):
    id: Slug
    title: Title
    aliases: list[Title] = Field(default_factory=list, max_length=16)
    materials: list[Role] = Field(max_length=12)
    steps: list[Step] = Field(min_length=1, max_length=12)
    rules: list[Rule] = Field(default_factory=list, max_length=16)
    evidence: list[Evidence] = Field(default_factory=list, max_length=16)
