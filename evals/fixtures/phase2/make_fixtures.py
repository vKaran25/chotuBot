"""Regenerate the synthetic Phase 2 test documents (public, invented content; safe for Git).

    python evals/fixtures/phase2/make_fixtures.py

Writes small text PDFs by hand (no PDF library needed), so the bytes are fully reproducible.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

HANDBOOK_PAGES = [
    [
        "Orion Rover Handbook",
        "",
        "The Orion rover is a small research vehicle built by the fictional Kestrel Lab.",
        "Its main battery lasts 14 hours on a full charge at room temperature.",
        "Charging from empty to full takes 3 hours with the standard dock.",
    ],
    [
        "Maintenance",
        "",
        "Replace the wheel bearings every 400 kilometres of driving.",
        "The dust filter must be cleaned after each field day.",
        "Firmware updates are installed only from the signed Kestrel update server.",
    ],
]

NOTES_MD = """# Orion field notes

Notes from the fictional Kestrel Lab field team.

## Ladakh trip

The Orion rover was field tested in Ladakh in March.
Night temperatures dropped to minus 20 degrees Celsius.
In the cold the battery lasted about 9 hours instead of the usual figure.

## Lessons

- Keep the battery pack insulated overnight.
- Carry a spare dust filter.

```
# not a heading: this line is inside a code block
```

## Next steps

The team plans a desert trial in Rajasthan next spring.
"""

TRAVEL_TXT = """Kestrel Lab budget memo (invented example)

The field programme budget for this year is 42 lakh rupees.
Most of it pays for transport, insurance and spare parts.

The Ladakh trip cost 6 lakh rupees in total.
The planned Rajasthan desert trial is estimated at 4 lakh rupees.

Approval for spending above 1 lakh rupees comes from the lab director, Dr. Meera Rao.
"""


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal valid PDF 1.4 with one Helvetica text stream per page."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [3 + 2 * i for i in range(n_pages)]
    font_id = 3 + 2 * n_pages
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content_id = page_ids[i] + 1
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def main() -> None:
    (HERE / "orion-handbook.pdf").write_bytes(make_pdf(HANDBOOK_PAGES))
    (HERE / "orion-field-notes.md").write_bytes(NOTES_MD.encode("utf-8"))
    (HERE / "kestrel-budget.txt").write_bytes(TRAVEL_TXT.encode("utf-8"))
    (HERE / "no-text.pdf").write_bytes(make_pdf([[]]))  # a page with no text layer (like a scan)
    (HERE / "corrupt.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R\nthis is not a pdf")
    print("wrote fixtures to", HERE)


if __name__ == "__main__":
    main()
