"""Versioned applicability rules and model evaluation scope."""
from datetime import date
from typing import Literal

from pydantic import Field

from guizhi.domain.skills import Strict, Title


class Citation(Strict):
    document_run: str
    fragment: str
    quote: str = Field(min_length=1, max_length=800)


class RuleEdition(Strict):
    family: Title
    edition: Title
    title: Title
    region: Title
    matter: Title
    project_code: Title | None = None
    valid_from: date
    valid_until: date | None = None
    transition: str = Field(default="", max_length=400)
    supersedes: list[str] = Field(default_factory=list, max_length=16)
    basis: Citation


class Term(Strict):
    source: Title
    meaning: Title
    basis: Citation


class ModelScope(Strict):
    provider: Title
    model: Title
    version: Title
    regions: list[Title] = Field(min_length=1, max_length=24)
    tasks: list[Literal["extract", "match", "predict", "explain"]] = Field(min_length=1, max_length=4)
    assessment: Citation
