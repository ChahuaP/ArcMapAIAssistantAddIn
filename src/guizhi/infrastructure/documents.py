"""Read sealed approval materials and retain exact, source-native locators."""
from functools import lru_cache
from zipfile import ZipFile

import pypdfium2

from guizhi.infrastructure.optional_components import require


@lru_cache(maxsize=1)
def ocr_engine():
    require("ocr")
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def pdf_fragments(path, check):
    with pypdfium2.PdfDocument(path) as document:
        if len(document) > 150:
            raise ValueError("PDF exceeds 150 pages")
        for page_number, page in enumerate(document, 1):
            check()
            text_page = page.get_textpage()
            text = text_page.get_text_range().strip()
            if text:
                left, bottom, right, top = page.get_bbox()
                yield {"page": page_number, "box": [left, bottom, right, top],
                       "coordinates": "pdf_points", "method": "text"}, text
                continue
            engine = ocr_engine()
            image = page.render(scale=2, rev_byteorder=True).to_numpy()
            results, _ = engine(image)
            for quad, line, score in results or []:
                check()
                if line.strip():
                    yield {"page": page_number, "polygon": quad, "size": [image.shape[1], image.shape[0]],
                           "coordinates": "image_pixels", "method": "ocr",
                           "confidence": round(float(score), 4)}, line
            if not results:
                yield {"page": page_number, "method": "ocr_empty"}, ""


def checked_office(path):
    with ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 150 << 20:
            raise ValueError("Expanded office document exceeds 150 MB")


def word_fragments(path, check):
    from docx import Document
    from docx.table import Table

    checked_office(path)
    doc = Document(path)
    for i, block in enumerate(doc.iter_inner_content(), 1):
        check()
        if isinstance(block, Table):
            for j, row in enumerate(block.rows, 1):
                for k, cell in enumerate(row.cells, 1):
                    if cell.text.strip():
                        yield {"block": i, "row": j, "column": k, "method": "table_cell"}, cell.text
        elif block.text.strip():
            yield {"block": i, "method": "paragraph"}, block.text


def sheet_fragments(path, check):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    checked_office(path)
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        for sheet in book:
            for row_no, row in enumerate(sheet.iter_rows(values_only=True), 1):
                check()
                for col_no, value in enumerate(row, 1):
                    if value is not None:
                        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
                        yield {"sheet": sheet.title, "cell": f"{get_column_letter(col_no)}{row_no}",
                               "method": "cell"}, text
    finally:
        book.close()


def read_text_data(path):
    raw = path.read_bytes()
    try:
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError as error:
            raise ValueError(f"{path.suffix[1:].upper()} 编码无法识别，请另存为 UTF-8 后添加") from error
    if "\0" in text:
        raise ValueError(f"{path.suffix[1:].upper()} 含有无效的文本字符，请另存为 UTF-8 后添加")
    return text


def text_fragments(path, check):
    for i, line in enumerate(read_text_data(path).splitlines(), 1):
        check()
        if line.strip():
            yield {"line": i, "method": "text"}, line


def fragments(path, check):
    parse = {".pdf": pdf_fragments, ".docx": word_fragments, ".xlsx": sheet_fragments,
             ".csv": text_fragments, ".txt": text_fragments}[path.suffix.lower()]
    for i, (locator, text) in enumerate(parse(path, check), 1):
        if i > 25000 or len(text) > 100000:
            raise ValueError("Document exceeds the supported extraction size")
        yield locator, text
