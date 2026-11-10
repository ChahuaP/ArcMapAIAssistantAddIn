"""Export attributes from a processed vector dataset."""
import csv

import pandas as pd

from guizhi.infrastructure.operations.common import field


def export_table(ctx, fields: list[str] | None = None):
    frame = ctx.frames[0]
    if fields is not None:
        if not fields or len(set(fields)) != len(fields):
            raise ValueError("Fields must be a nonempty unique list")
        for name in fields:
            field(frame, name)
    attributes = frame[fields] if fields else frame.drop(columns=frame.geometry.name)
    if not len(attributes.columns):
        raise ValueError("Layer has no attribute columns to export")
    file = ctx.path("table.csv")
    with file.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(attributes.columns)
        for row in attributes.itertuples(index=False, name=None):
            writer.writerow([None if pd.isna(value) else value for value in row])
    with file.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        if next(reader) != list(attributes.columns) or sum(1 for _ in reader) != len(attributes):
            raise ValueError("CSV failed round-trip verification")
    ctx.record("table", file)
    return {"statistics": {"row_count": len(attributes)}}
