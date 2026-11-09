"""Validate declared delivery attachments against their sealed revisions."""
from pathlib import Path
from typing import Literal
from zipfile import ZipFile

from pypdfium2 import PdfDocument

from guizhi.infrastructure.documents import checked_office


def check_attachments(ctx, required: list[Literal["pdf", "docx", "xlsx"]]):
    supplied = []
    names = set()
    for item, (file, ref) in zip(ctx.request.inputs, ctx.documents, strict=True):
        ctx.check()
        name = Path(item.file).name
        if name.casefold() in names:
            raise ValueError("Attachment names must be unique")
        names.add(name.casefold())
        suffix = file.suffix[1:].lower()
        if suffix == "pdf":
            with PdfDocument(file) as pdf:
                if not len(pdf):
                    raise ValueError("PDF attachment has no pages")
        else:
            checked_office(file)
            with ZipFile(file) as office:
                entry = "word/document.xml" if suffix == "docx" else "xl/workbook.xml"
                if entry not in office.namelist() or office.testzip() is not None:
                    raise ValueError("Office attachment content does not match its format")
        supplied.append({"name": name, "format": suffix, "sha256": ref["sha256"]})
    missing = sorted(set(required) - {item["format"] for item in supplied})
    if missing:
        raise ValueError(f"Missing required attachments: {', '.join(missing)}")
    ctx.json("attachments.json", {"attachments": supplied}, "attachments")
    return {"statistics": {"attachment_count": len(supplied)}}
