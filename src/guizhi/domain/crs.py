"""The explicit, versioned coordinate transformation contract."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Unit = Literal["degree", "m", "ft", "us-ft"]
XY = tuple[FiniteFloat, FiniteFloat]
XYZ = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Grid(Contract):
    name: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]+\.(tif|gsb|gtx)$")]
    sha256: Digest


class ControlPoint(Contract):
    source: XY | XYZ
    target: XY | XYZ


class TransformPlan(Contract):
    id: Slug
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    source_wkt: str
    target_wkt: str
    source_unit: Unit
    target_unit: Unit
    direction: Literal["forward", "inverse"]
    pipeline: Annotated[str, StringConstraints(min_length=1, max_length=12000)]
    area: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    accuracy_m: Annotated[FiniteFloat, Field(ge=0)]
    tolerance_m: Annotated[FiniteFloat, Field(gt=0)]
    controls: Annotated[list[ControlPoint], Field(min_length=1, max_length=100)]
    reference: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    grids: list[Grid] = Field(default_factory=list)
    dimension: Literal[2, 3] = 2
    area_kind: Literal["geographic", "source"] = "geographic"
    z_range: XY | None = None
