"""Produce a matched workbook and compact report from one verified set of rows."""
from html import escape
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
    html = ctx.path("report.html")
    heading = "".join(f"<th>{escape(str(value))}</th>" for value in columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(value if value is not None else ''))}</td>" for value in row)
                   + "</tr>" for row in rows)
    html.write_text(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>body{{max-width:850px;margin:45px auto;font:16px system-ui;color:#18363b}}"
        "table{border-collapse:collapse}th,td{border-bottom:1px solid #d8e4df;padding:9px 14px;text-align:left}</style>"
        f"<h1>{escape(title)}</h1><p>{escape(note)}</p><table><thead><tr>{heading}</tr></thead>"
        f"<tbody>{body}</tbody></table></html>", encoding="utf-8")
    ctx.record("report", html)
