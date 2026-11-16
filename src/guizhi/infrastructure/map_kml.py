"""Export the displayed vector symbology in KML's standard style elements."""
from base64 import b64decode
from binascii import Error as DecodeError
import json
from pathlib import Path
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from typing import Annotated
from urllib.parse import quote
import xml.etree.ElementTree as xml

import pyogrio
from pydantic import BaseModel, Field, StringConstraints

from guizhi.infrastructure.gdal_translate import vector_translate
from guizhi.infrastructure.operations.table import identifier


NS = "http://www.opengis.net/kml/2.2"
Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class Category(BaseModel):
    value: str | int | float | bool | None
    color: Hex
    fillColor: Hex | None = None


class Classification(BaseModel):
    field: str
    classes: list[Category] = Field(max_length=1000)
    other: Hex
    otherFill: Hex | None = None


class Symbol(BaseModel):
    color: Hex
    fill: Hex | None
    opacity: int = Field(ge=0, le=100)
    fillOpacity: int = Field(ge=0, le=100)
    noOutline: bool
    width: float = Field(ge=0.5, le=8)
    classification: Classification | None = None


def tag(name):
    return f"{{{NS}}}{name}"


def element(parent, name, text):
    xml.SubElement(parent, tag(name)).text = str(text)


def kml_color(color, opacity):
    red, green, blue = color[1:3], color[3:5], color[5:7]
    return f"{round(opacity * 255):02x}{blue}{green}{red}".lower()


def style_element(name, symbol, color, fill, geometry, href=None):
    node = xml.Element(tag("Style"), id=name)
    opacity = symbol.opacity / 100
    if "Point" in geometry:
        icon = xml.SubElement(node, tag("IconStyle"))
        element(icon, "color", kml_color(color, opacity))
        element(icon, "colorMode", "normal")
        element(xml.SubElement(icon, tag("Icon")), "href", href)
        xml.SubElement(icon, tag("hotSpot"), x="0.5", y="0.5", xunits="fraction", yunits="fraction")
    else:
        line = xml.SubElement(node, tag("LineStyle"))
        element(line, "color", kml_color(color, opacity))
        element(line, "colorMode", "normal")
        element(line, "width", symbol.width)
        if "Polygon" in geometry:
            poly = xml.SubElement(node, tag("PolyStyle"))
            element(poly, "color", kml_color(fill or color, opacity * symbol.fillOpacity / 100))
            element(poly, "colorMode", "normal")
            element(poly, "fill", int(fill is not None))
            element(poly, "outline", int(not symbol.noOutline))
    return node


def unpack_icon(icon):
    header = "data:image/png;base64,"
    if not isinstance(icon, str) or not icon.startswith(header) or len(icon) > 24_000:
        raise ValueError("无法生成 KML 点符号")
    try:
        data = b64decode(icon[len(header):], validate=True)
    except DecodeError as error:
        raise ValueError("无法生成 KML 点符号") from error
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[-8:] != b"IEND\xaeB`\x82":
        raise ValueError("KML 点符号格式无效")
    return data


def classified_input(file, layer, info, field):
    from guizhi.infrastructure.map_workspace import category_value

    fields = set(info["fields"])
    if field not in fields:
        raise ValueError("图层已无此分类字段，请重新设置符号化")
    names = {name.casefold() for name in fields}
    marker = "__xiaoliu_style_fid"
    while marker.casefold() in names:
        marker += "_"
    attrs = pyogrio.read_dataframe(file, layer=layer, columns=[field], read_geometry=False, fid_as_index=True)
    values = json.loads(attrs[field].to_json(orient="values", date_format="iso", force_ascii=False))
    categories = {str(fid): category_value(value) for fid, value in zip(attrs.index, values, strict=True)}
    query = f"SELECT *, CAST({identifier(info['fid_column'])} AS TEXT) AS {identifier(marker)} FROM {identifier(layer)}"
    return marker, categories, ("-dialect", "SQLITE", "-sql", query)


def add_styles(file, symbol, variants, categories, marker, geometry, href):
    from guizhi.infrastructure.map_workspace import category_value

    xml.register_namespace("", NS)
    tree = xml.parse(file)
    doc = tree.getroot().find(tag("Document"))
    if doc is None:
        raise ValueError("无法在导出的 KML 中定位图层")
    styles = []
    for index, (color, fill) in enumerate(variants):
        styles.append(style_element(f"xiaoliu-{index}", symbol, color, fill, geometry, href))
    after_header = next((i for i, child in enumerate(doc) if child.tag in {tag("Schema"), tag("Folder"), tag("Placemark")}), len(doc))
    doc[after_header:after_header] = styles
    if marker:
        for schema in doc.iter(tag("Schema")):
            for field in list(schema):
                if field.get("name") == marker:
                    schema.remove(field)
    indexed = {category_value(row.value): i for i, row in enumerate(symbol.classification.classes, 1)} if marker else {}
    style_tags = {tag(name) for name in ("Style", "StyleMap", "styleUrl")}
    contents = {tag(name) for name in ("ExtendedData", "Point", "LineString", "Polygon", "MultiGeometry")}
    for placemark in doc.iter(tag("Placemark")):
        index = 0
        if marker:
            for data in placemark.iter(tag("SchemaData")):
                entry = next((child for child in data if child.get("name") == marker), None)
                if entry is not None:
                    if entry.text not in categories:
                        raise ValueError("无法匹配 KML 要素的分类符号")
                    index = indexed.get(categories[entry.text], 0)
                    data.remove(entry)
                    break
            else:
                raise ValueError("KML 导出缺少分类要素编号")
        for child in list(placemark):
            if child.tag in style_tags:
                placemark.remove(child)
        position = next((i for i, child in enumerate(placemark) if child.tag in contents), len(placemark))
        link = xml.Element(tag("styleUrl"))
        link.text = f"#xiaoliu-{index}"
        placemark.insert(position, link)
    tree.write(file, encoding="utf-8", xml_declaration=True)


def export_kml(file, layer, output, payload):
    try:
        symbol = Symbol.model_validate(payload.get("style"))
    except ValueError as error:
        raise ValueError("无法读取图层符号，请重新打开导出菜单") from error
    info = pyogrio.read_info(file, layer=layer)
    geometry = info["geometry_type"]
    classification = symbol.classification
    variant = [(symbol.color, symbol.fill)]
    marker = categories = None
    query = (layer,)
    if classification:
        marker, categories, query = classified_input(file, layer, info, classification.field)
        other = classification.otherFill if "otherFill" in classification.model_fields_set else classification.other
        variant = [(classification.other, other if symbol.fill is not None else None)]
        for row in classification.classes:
            fill = row.fillColor if "fillColor" in row.model_fields_set else row.color
            variant.append((row.color, fill if symbol.fill is not None else None))
    icon = unpack_icon(payload.get("pointIcon")) if "Point" in geometry else None
    icon_path = output.with_name(output.stem + "_符号.png") if icon else None
    href = quote(icon_path.name) if icon_path else None
    title = payload.get("name") or layer
    with TemporaryDirectory(dir=output.parent) as directory:
        draft = Path(directory) / "output.kml"
        vector_translate(file, draft, "-f", "KML", "-t_srs", "EPSG:4326", "-nln", title, *query)
        add_styles(draft, symbol, variant, categories, marker, geometry, href)
        written = []
        try:
            if icon_path:
                with icon_path.open("xb") as stream:
                    written.append(icon_path)
                    stream.write(icon)
            with draft.open("rb") as src, output.open("xb") as target:
                written.append(output)
                copyfileobj(src, target)
        except OSError:
            for path in written:
                path.unlink(missing_ok=True)
            raise
