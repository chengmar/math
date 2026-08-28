"""Extract paragraphs, tables, and embedded media from converted DOCX inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from docx import Document
<SOURCE_FILE_REDACTED>ument import Document as _Document
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P


def iter_blocks(parent: _Document | _Cell):
    """Yield paragraphs and tables in their document order."""
    parent_element = parent.element.body if isinstance(parent, _Document) else parent._tc
    for child in parent_element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def clean_cell(cell: _Cell) -> str:
    return "\n".join(p.text.strip() for p in cell.paragraphs if p.text.strip())


def extract_document(path: Path, media_root: Path) -> dict:
    document = Document(path)
    blocks: list[dict] = []
    table_index = 0
    paragraph_index = 0
    for block in iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                blocks.append(
                    {
                        "type": "paragraph",
                        "index": paragraph_index,
                        "style": block.style.name if block.style else None,
                        "text": text,
                    }
                )
            paragraph_index += 1
        else:
            rows = [[clean_cell(cell) for cell in row.cells] for row in block.rows]
            blocks.append({"type": "table", "index": table_index, "rows": rows})
            table_index += 1

    media_dir = media_root / path.stem
    media: list[dict] = []
<SOURCE_FILE_REDACTED>File(path) as archive:
        for member in sorted(archive.namelist()):
            if not member.startswith("word/media/") or member.endswith("/"):
                continue
            payload = archive.read(member)
            media_dir.mkdir(parents=True, exist_ok=True)
            destination = media_dir / Path(member).name
            destination.write_bytes(payload)
            media.append(
                {
                    "file": destination.as_posix(),
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )

    return {
        "source": path.as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "paragraph_count": paragraph_index,
        "table_count": table_index,
        "inline_shape_count": len(document.inline_shapes),
        "blocks": blocks,
        "media": media,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    media_root = args.output_dir / "media"
    summary = []
    for path in sorted(args.input_dir.glob("*.docx")):
        record = extract_document(path, media_root)
        output_path = args.output_dir / f"{path.stem}.json"
        output_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary.append(
            {
                "document": path.name,
                "paragraphs": record["paragraph_count"],
                "tables": record["table_count"],
                "media": len(record["media"]),
            }
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
