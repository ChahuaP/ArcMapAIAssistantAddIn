"""Area conversion used by geometry fields, aggregate evidence and its presentation."""
from typing import Literal

AreaUnit = Literal["square_meters", "mu", "hectares", "square_kilometers"]
M2_PER_UNIT = {"square_meters": 1, "mu": 10_000 / 15, "hectares": 10_000, "square_kilometers": 1_000_000}
AREA_LABELS = {"square_meters": "平方米", "mu": "亩", "hectares": "公顷", "square_kilometers": "平方千米"}


def area_values(square_meters):
    return {unit: square_meters / divisor if square_meters is not None else None for unit, divisor in M2_PER_UNIT.items()}
