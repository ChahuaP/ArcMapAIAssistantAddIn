
"""Contracts for a scoped multispectral pixel classifier."""
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from guizhi.domain.capabilities import Hash
from guizhi.domain.skills import Strict, Title


class ModelProfile(Strict):
    name: Title
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    sha256: Hash
    evaluation: Literal["simulation", "field"]
    region: Title
    sensor: Title
    start: date
    end: date
    epsg: Annotated[int, Field(gt=0)]
    bounds: tuple[float, float, float, float]
    resolution_m: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    bands: list[Title] = Field(min_length=2, max_length=16)
    scale: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    sample_min: Annotated[float, Field(allow_inf_nan=False)]
    sample_max: Annotated[float, Field(allow_inf_nan=False)]
    min_confidence: Annotated[float, Field(gt=0, le=1)]
    classes: list[Title] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def consistent(self):
        from math import isfinite

        xmin, ymin, xmax, ymax = self.bounds
        if (self.start > self.end or self.sample_min >= self.sample_max or
                not all(map(isfinite, self.bounds)) or xmin >= xmax or ymin >= ymax or
                len(set(self.bands)) != len(self.bands) or len(set(self.classes)) != len(self.classes)):
            raise ValueError("Model profile has inconsistent ranges, bands or classes")
        return self


class Classification(Strict):
    model: ModelProfile
    observed_on: date
    unknown_code: Literal[0] = 0
    nodata_code: Literal[255] = 255
