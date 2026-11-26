#!/usr/bin/env python3
"""Build the current-system approval guide using the established DOCX renderer.

Run with /usr/bin/python3. The adjacent build_user_manual.py and Markdown are
required. No Django initialization or database access is performed here.
"""

import importlib.util
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/EDCS_Financial_Year_End_Approval_Guide.md"
OUTPUT = SOURCE.with_suffix(".docx")


def main():
    spec = importlib.util.spec_from_file_location(
        "edcs_guide_renderer", Path(__file__).with_name("build_user_manual.py")
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    renderer.SOURCE = SOURCE
    renderer.OUTPUT = OUTPUT
    renderer.build({})
    with zipfile.ZipFile(OUTPUT) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["word/header1.xml"] = parts["word/header1.xml"].replace(
        b"USER MANUAL", b"FINANCIAL YEAR-END APPROVAL GUIDE"
    )
    parts["word/footer1.xml"] = (
        parts["word/footer1.xml"]
        .replace(b"EDCS-UM-001", b"EDCS-YEA-001")
        .replace(b"v1.1", b"v1.0")
        .replace(b"15 November 2026", b"26 November 2026")
    )
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    # Give screenshots more space than the general manual's half-page figure cap.
    # Scaling changes the document presentation only; PNG captures stay intact.
    tree = ET.fromstring(parts["word/document.xml"])
    # Keep the reusable account worksheet on one printable page.
    for table in tree.findall(f".//{w}tbl"):
        first = table.find(f"{w}tr/{w}tc")
        if first is None or "".join(first.itertext()) != "Record field":
            continue
        widths = (4000, 5860)
        for node, width in zip(table.findall(f"{w}tblGrid/{w}gridCol"), widths):
            node.set(f"{w}w", str(width))
        for row in table.findall(f"{w}tr"):
            for cell, width in zip(row.findall(f"{w}tc"), widths):
                cell.find(f"{w}tcPr/{w}tcW").set(f"{w}w", str(width))
        for side in ("top", "bottom"):
            table.find(f"{w}tblPr/{w}tblCellMar/{w}{side}").set(f"{w}w", "45")
    ns = {
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    for drawing in tree.findall(f".//{w}drawing"):
        extent = drawing.find(".//wp:extent", ns)
        if extent is None:
            continue
        cx, cy = int(extent.get("cx")), int(extent.get("cy"))
        if cx < 2000000 or cy < 4000000:
            continue
        factor = min(6200000 / cx, 5650000 / cy)
        if factor <= 1:
            continue
        for node in [extent, *drawing.findall(".//a:xfrm/a:ext", ns)]:
            node.set("cx", str(int(cx * factor)))
            node.set("cy", str(int(cy * factor)))
    parts["word/document.xml"] = ET.tostring(
        tree, encoding="utf-8", xml_declaration=True
    )
    core = ET.fromstring(parts["docProps/core.xml"])
    metadata = {
        "title": "EDCS Financial Year-End Closing and Account Approval Guide",
        "subject": "Current approval modules, level configuration, year-end workflows and verified limitations",
        "description": "Prepared 26 November 2026. Actual current application screenshots with fictional training records. Live records were not changed.",
    }
    for name, value in metadata.items():
        node = core.find(f"{{http://purl.org/dc/elements/1.1/}}{name}")
        if node is not None:
            node.text = value
    parts["docProps/core.xml"] = ET.tostring(
        core, encoding="utf-8", xml_declaration=True
    )
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    print(
        json.dumps(
            {
                "docx": str(OUTPUT),
                "source": str(SOURCE),
                "screenshots": len(
                    [name for name in parts if name.startswith("word/media/")]
                )
                - 1,
            }
        )
    )


if __name__ == "__main__":
    main()
