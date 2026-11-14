"""Produce a matched workbook and compact report from one verified set of rows."""
from math import isfinite

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference


def publish_report(ctx, title, columns, rows, *, measure=None, note=""):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "核查结果"
    sheet.append(columns)
    for row in rows:
        ctx.check()
        if len(row) != len(columns) or any(isinstance(value, float) and not isfinite(value) for value in row):
            raise ValueError("Report row contains invalid values")
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    if measure and rows:
        chart = BarChart()
        chart.title = columns[measure - 1]
        chart.add_data(Reference(sheet, min_col=measure, min_row=1, max_row=len(rows) + 1), titles_from_data=True)
        chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(rows) + 1))
        sheet.add_chart(chart, f"A{len(rows) + 4}")
    xlsx = ctx.path("report.xlsx")
    workbook.save(xlsx)
    checked = load_workbook(xlsx, read_only=True)
    try:
        for saved, expected in zip(checked.active.values, (columns, *rows), strict=True):
            if saved != tuple(expected):
                if any(not isinstance(left, float) or not isinstance(right, float) or
                       abs(left - right) > max(abs(right), 1) * 1e-12
                       for left, right in zip(saved, expected, strict=True) if left != right):
                    raise ValueError("Report workbook values did not round-trip")
    finally:
        checked.close()
    ctx.record("table", xlsx)
    ctx.json("report.json", {"title": title, "note": note,
                            "tables": [{"title": "办理明细", "columns": columns, "rows": rows}]}, "report")
