"""Structured proposals require a source reference and distinguish knowledge from calculation."""
from datetime import date
import re
import unicodedata
from typing import Literal

from pydantic import Field

from guizhi.domain.skills import Strict


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def code(value):
    return re.sub(r"[\s-]+", "", normalized(value))


class Fact(Strict):
    field: str = Field(min_length=1, max_length=90)
    value: str = Field(min_length=1, max_length=200)
    fragment: str = Field(min_length=1, max_length=90)
    quote: str = Field(min_length=1, max_length=400, description="Verbatim excerpt from the selected source fragment")
    observed_on: date | None = None
    polarity: Literal["affirmed", "negated", "conditional"] = "affirmed"
    qualifier: str = Field(default="", max_length=300,
                           description="Verbatim words inside quote, e.g. 'subject to the conditions', with no translation")


class Transition(Strict):
    previous: list[str] = Field(min_length=1, max_length=32)
    current: list[str] = Field(min_length=1, max_length=32)
