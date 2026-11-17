#!/usr/bin/env python3
"""Build the editable EDCS manual from its Markdown and screenshot assets.

Run with a Python installation providing the Markdown package (on the reviewed
host: /usr/bin/python3 docs/tools/build_user_manual.py). LibreOffice can convert
the resulting DOCX to PDF. Optional --page-map supplies cached contents pages
as a JSON object mapping chapter number strings to PDF page numbers.
"""

import argparse
import json
import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import markdown

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
SOURCE = DOCS / "EDCS_User_Manual.md"
OUTPUT = DOCS / "EDCS_User_Manual.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("w", W)
ET.register_namespace("r", R)


def el(tag, attrs=None, text=None):
    node = ET.Element(
        f"{{{W}}}{tag}",
        {f"{{{W}}}{key}": str(value) for key, value in (attrs or {}).items()},
    )
    if text is not None:
        node.text = text
    return node


def add(parent, tag, attrs=None, text=None):
    node = el(tag, attrs, text)
    parent.append(node)
    return node


def xml(node):
    return ET.tostring(node, encoding="utf-8", xml_declaration=True)


def build(page_map):
    document = el("document")
    body = add(document, "body")
    relationships = []
    media = []
    heading_bookmarks = {}
    html = markdown.markdown(SOURCE.read_text(), extensions=["tables", "fenced_code"])
    source = ET.fromstring("<root>" + html + "</root>")
    for node in source.findall("h2"):
        title = "".join(node.itertext())
        number = re.match(r"^(\d+)\.", title).group(1)
        anchor = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        heading_bookmarks[anchor] = f"chapter_{number}"

    def run(parent, text, bold=False, italic=False, code=False, color=None):
        if not text:
            return
        r = add(parent, "r")
        if bold or italic or code or color:
            props = add(r, "rPr")
            if bold:
                add(props, "b")
            if italic:
                add(props, "i")
            if color:
                add(props, "color", {"val": color})
            if code:
                add(
                    props,
                    "rFonts",
                    {"ascii": "DejaVu Sans Mono", "hAnsi": "DejaVu Sans Mono"},
                )
                add(props, "sz", {"val": 17})
        for index, line in enumerate(text.split("\n")):
            if index:
                add(r, "br")
            t = add(r, "t", text=line)
            t.set(f"{{{XML}}}space", "preserve")

    def inline(parent, node, bold=False, italic=False, code=False):
        run(parent, node.text, bold, italic, code)
        for child in node:
            if child.tag == "br":
                add(add(parent, "r"), "br")
            elif child.tag == "a" and child.get("href", "").startswith("#"):
                anchor = heading_bookmarks.get(child.get("href")[1:])
                if anchor:
                    link = add(parent, "hyperlink", {"anchor": anchor, "history": 1})
                    inline(link, child, bold, italic, code)
                else:
                    inline(parent, child, bold, italic, code)
            else:
                inline(
                    parent,
                    child,
                    bold or child.tag in ("strong", "b"),
                    italic or child.tag in ("em", "i"),
                    code or child.tag == "code",
                )
            run(parent, child.tail, bold, italic, code)

    def paragraph(parent=body, style="Normal", node=None, text=None, page=False):
        p = add(parent, "p")
        props = add(p, "pPr")
        add(props, "pStyle", {"val": style})
        if page:
            add(props, "pageBreakBefore")
        if node is not None:
            inline(p, node)
        if text:
            run(p, text)
        return p

    def field(parent, instruction, cached="1"):
        result = add(parent, "fldSimple", {"instr": instruction})
        run(result, cached)

    def image(path, alt, cover=False):
        if not path.is_file():
            raise FileNotFoundError(path)
        width, height = struct.unpack(">II", path.read_bytes()[16:24])
        cx = 1250000 if cover else 6200000
        cy = int(cx * height / width)
        if not cover and cy > 4050000:
            cx = int(cx * 4050000 / cy)
            cy = 4050000
        number = len(media) + 1
        rid = f"rIdImage{number}"
        target = f"media/image{number}.png"
        relationships.append((rid, "image", target))
        media.append((path, "word/" + target))
        p = paragraph(style="CoverLogo" if cover else "Figure")
        drawing = f"""<w:r xmlns:w="{W}"><w:drawing>
<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{number}" name="Figure {number}" descr=""/>
<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="image{number}.png"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip xmlns:r="{R}" r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"""
        drawing_node = ET.fromstring(drawing)
        drawing_node.find(
            ".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"
        ).set("descr", alt)
        p.append(drawing_node)
        if not cover:
            paragraph(style="Caption", text=alt)

    image(ROOT / "static/images/edcs-logo.png", "EDCS product logo", cover=True)
    in_contents = False
    after_contents = False
    for node in source:
        tag = node.tag
        text = "".join(node.itertext())
        if tag == "h1":
            paragraph(style="Title", node=node)
        elif tag == "h2":
            after_contents = True
            in_contents = False
            p = paragraph(style="Heading1", node=node, page=True)
            number = re.match(r"^(\d+)\.", text).group(1)
            p.insert(
                1, el("bookmarkStart", {"id": number, "name": f"chapter_{number}"})
            )
            add(p, "bookmarkEnd", {"id": number})
        elif tag == "h3":
            in_contents = text == "Contents"
            paragraph(
                style="Heading1" if not after_contents else "Heading2",
                node=node,
                page=in_contents or text == "How to use this manual",
            )
        elif tag == "p" and node.find("img") is not None:
            picture = node.find("img")
            image(DOCS / picture.get("src"), picture.get("alt", ""))
        elif tag == "p":
            style = (
                "CoverSubtitle" if text == "EDCS Member Management System" else "Normal"
            )
            paragraph(style=style, node=node)
        elif tag in ("ol", "ul"):
            for index, item in enumerate(node.findall("li"), 1):
                if in_contents:
                    p = paragraph(style="Contents")
                    inline(p, item)
                    add(add(p, "r"), "tab")
                    anchor = f"chapter_{index}"
                    field(p, f"PAGEREF {anchor} \\h", str(page_map.get(str(index), "")))
                else:
                    p = paragraph(style="ListParagraph")
                    run(p, f"{index}. " if tag == "ol" else "• ")
                    inline(p, item)
        elif tag == "table":
            table = add(body, "tbl")
            props = add(table, "tblPr")
            add(props, "tblW", {"w": 9860, "type": "dxa"})
            add(props, "tblLayout", {"type": "fixed"})
            borders = add(props, "tblBorders")
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                add(borders, side, {"val": "single", "sz": 4, "color": "D9DEE4"})
            margins = add(props, "tblCellMar")
            first_header = node.find("./thead/tr/th")
            compact = (
                first_header is not None and "".join(first_header.itertext()) == "Field"
            )
            for side in ("top", "bottom"):
                add(margins, side, {"w": 45 if compact else 75, "type": "dxa"})
            for side in ("left", "right"):
                add(margins, side, {"w": 100, "type": "dxa"})
            rows = node.findall("./thead/tr") + node.findall("./tbody/tr")
            columns = len(rows[0])
            widths = {
                2: [3000, 6860],
                3: [2150, 3855, 3855],
                4: [3050, 2110, 2000, 2700],
            }[columns]
            headers = ["".join(cell.itertext()) for cell in rows[0]]
            if headers[0] in ("Problem or message", "What you need"):
                widths = [3200, 6660] if columns == 2 else [2550, 3150, 4160]
            if headers[0] == "Task":
                widths = [3250, 6610]
            grid = add(table, "tblGrid")
            for width in widths:
                add(grid, "gridCol", {"w": width})
            for row_index, row in enumerate(rows):
                tr = add(table, "tr")
                tr_props = add(tr, "trPr")
                add(tr_props, "cantSplit")
                if row_index == 0:
                    add(tr_props, "tblHeader")
                for col_index, cell in enumerate(row):
                    tc = add(tr, "tc")
                    tc_props = add(tc, "tcPr")
                    add(tc_props, "tcW", {"w": widths[col_index], "type": "dxa"})
                    add(tc_props, "vAlign", {"val": "top"})
                    fill = (
                        "6B2138"
                        if row_index == 0
                        else ("F5F6F8" if row_index % 2 else "FFFFFF")
                    )
                    add(tc_props, "shd", {"fill": fill, "val": "clear"})
                    paragraph(
                        parent=tc,
                        style="TableHeader" if row_index == 0 else "TableText",
                        node=cell,
                    )
            paragraph(style="TableAfter")
        elif tag == "pre":
            for line in text.rstrip().splitlines():
                paragraph(style="Code", text=line)

    section = add(body, "sectPr")
    for tag, rid in (
        ("headerReference", "rIdHeader"),
        ("footerReference", "rIdFooter"),
    ):
        ref = add(section, tag, {"type": "default"})
        ref.set(f"{{{R}}}id", rid)
    add(section, "pgSz", {"w": 11906, "h": 16838})
    add(
        section,
        "pgMar",
        {
            "top": 1023,
            "right": 1023,
            "bottom": 1023,
            "left": 1023,
            "header": 450,
            "footer": 450,
            "gutter": 0,
        },
    )
    add(section, "titlePg")

    styles = el("styles")

    def style(
        sid,
        name,
        size=21,
        bold=False,
        color="263442",
        before=0,
        after=115,
        keep=False,
        outline=None,
    ):
        s = add(styles, "style", {"type": "paragraph", "styleId": sid})
        add(s, "name", {"val": name})
        if sid != "Normal":
            add(s, "basedOn", {"val": "Normal"})
        p = add(s, "pPr")
        add(
            p,
            "spacing",
            {"before": before, "after": after, "line": 264, "lineRule": "auto"},
        )
        add(p, "widowControl")
        if keep:
            add(p, "keepNext")
        if outline is not None:
            add(p, "outlineLvl", {"val": outline})
        r = add(s, "rPr")
        add(r, "rFonts", {"ascii": "Noto Sans", "hAnsi": "Noto Sans"})
        add(r, "sz", {"val": size})
        add(r, "color", {"val": color})
        if bold:
            add(r, "b")
        return p

    style("Normal", "Normal")
    style("Title", "Title", size=66, bold=True, color="6B2138", before=350, after=180)
    style("CoverSubtitle", "Cover subtitle", size=35, color="6B2138", after=500)
    style("CoverLogo", "Cover logo", before=800, after=250)
    style(
        "Heading1",
        "Heading 1",
        size=32,
        bold=True,
        color="6B2138",
        before=80,
        after=260,
        keep=True,
        outline=0,
    )
    style(
        "Heading2",
        "Heading 2",
        size=24,
        bold=True,
        color="6B2138",
        before=220,
        after=130,
        keep=True,
        outline=1,
    )
    p = style("ListParagraph", "List Paragraph", after=100)
    add(p, "ind", {"left": 300, "hanging": 300})
    p = style("Contents", "Contents", size=20, after=105)
    tabs = add(p, "tabs")
    add(tabs, "tab", {"val": "right", "leader": "dot", "pos": 9760})
    for sid, name, bold, color in (
        ("TableText", "Table text", False, "263442"),
        ("TableHeader", "Table header", True, "FFFFFF"),
    ):
        p = style(sid, name, size=18, bold=bold, color=color, after=35)
        p.find(f"{{{W}}}spacing").set(f"{{{W}}}line", "240")
    style("TableAfter", "Table after", size=4, after=60)
    p = style("Figure", "Figure", after=70, before=100, keep=True)
    add(p, "jc", {"val": "center"})
    p = style("Caption", "Caption", size=17, color="5E6773", after=170)
    add(p, "jc", {"val": "center"})
    style("Code", "Code", size=17, after=70)
    style("Header", "Header", size=16, color="6B2138", after=0)
    style("Footer", "Footer", size=16, color="5E6773", after=0)
    header = el("hdr")
    paragraph(parent=header, style="Header", text="EDCS  /  USER MANUAL")
    footer = el("ftr")
    p = paragraph(
        parent=footer, style="Footer", text="EDCS-UM-001  •  v1.1  •  15 November 2026"
    )
    tabs = add(p.find(f"{{{W}}}pPr"), "tabs")
    add(tabs, "tab", {"val": "right", "pos": 9760})
    add(add(p, "r"), "tab")
    run(p, "Page ")
    field(p, "PAGE")
    run(p, " of ")
    field(p, "NUMPAGES")
    settings = el("settings")
    add(settings, "updateFields", {"val": "true"})

    def rels(items):
        root = ET.Element(
            "Relationships",
            xmlns="http://schemas.openxmlformats.org/package/2006/relationships",
        )
        for rid, kind, target in items:
            ET.SubElement(
                root, "Relationship", Id=rid, Type=R + "/" + kind, Target=target
            )
        return xml(root)

    types = ET.Element(
        "Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types"
    )
    for extension, mime in (
        ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
        ("xml", "application/xml"),
        ("png", "image/png"),
    ):
        ET.SubElement(types, "Default", Extension=extension, ContentType=mime)
    for part, mime in (
        ("document", "document.main"),
        ("styles", "styles"),
        ("settings", "settings"),
        ("header1", "header"),
        ("footer1", "footer"),
    ):
        ET.SubElement(
            types,
            "Override",
            PartName=f"/word/{part}.xml",
            ContentType=f"application/vnd.openxmlformats-officedocument.wordprocessingml.{mime}+xml",
        )
    ET.SubElement(
        types,
        "Override",
        PartName="/docProps/core.xml",
        ContentType="application/vnd.openxmlformats-package.core-properties+xml",
    )
    core = b"""<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EDCS User Manual</dc:title><dc:subject>Operations and administration, version 1.1</dc:subject><dc:creator>EDCS project documentation</dc:creator><dc:description>All modules, administration, contribution receipts and approval workflows. Prepared 15 November 2026. Screenshots contain fictional training records.</dc:description></cp:coreProperties>"""
    relationships += [
        ("rIdStyles", "styles", "styles.xml"),
        ("rIdSettings", "settings", "settings.xml"),
        ("rIdHeader", "header", "header1.xml"),
        ("rIdFooter", "footer", "footer1.xml"),
    ]
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in {
            "[Content_Types].xml": xml(types),
            "_rels/.rels": rels(
                [
                    ("rIdDocument", "officeDocument", "word/document.xml"),
                    ("rIdCore", "metadata/core-properties", "docProps/core.xml"),
                ]
            ).replace(
                (R + "/metadata/core-properties").encode(),
                b"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
            ),
            "word/document.xml": xml(document),
            "word/styles.xml": xml(styles),
            "word/settings.xml": xml(settings),
            "word/header1.xml": xml(header),
            "word/footer1.xml": xml(footer),
            "word/_rels/document.xml.rels": rels(relationships),
            "docProps/core.xml": core,
        }.items():
            z.writestr(name, data)
        for path, name in media:
            z.write(path, name)
    print(
        f"Built {OUTPUT}: {len(heading_bookmarks)} chapters, {len(media) - 1} screenshots"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-map", type=Path)
    args = parser.parse_args()
    build(json.loads(args.page_map.read_text()) if args.page_map else {})
