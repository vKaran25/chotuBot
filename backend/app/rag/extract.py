"""File validation and text extraction for text PDFs, Markdown and TXT (P2-REQ-2, P2-REQ-3). No OCR."""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from app.rag.config import MAX_FILE_BYTES, MAX_FILENAME_CHARS
from app.rag.errors import DocumentError

ALLOWED_TYPES = {".pdf": "pdf", ".md": "md", ".txt": "txt"}
EXTENSIONS = {v: k for k, v in ALLOWED_TYPES.items()}
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")

logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed-PDF chatter is reported as an API error instead


@dataclass(frozen=True)
class Section:
    """A part of a document that chunks never cross: one PDF page, one Markdown section, or a whole TXT file."""

    key: str  # stable within a document: "p3", "s0", "all"
    text: str
    base_line: int  # 1-based line of text[0] in the document; 0 when lines are meaningless (PDF)
    location: dict[str, Any] = field(default_factory=dict)  # {"page": n} or {"heading": "A > B"} or {}


@dataclass(frozen=True)
class Extracted:
    file_type: str
    sections: list[Section]
    page_count: int | None


def clean_filename(name: str | None) -> str:
    """Base name only (both / and \\ separators), control characters removed, trimmed, length-capped."""
    raw = name or ""
    base = PureWindowsPath(PurePosixPath(raw.replace("\\", "/")).name).name
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    if len(base) > MAX_FILENAME_CHARS:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: MAX_FILENAME_CHARS - len(ext) - 1] + dot + ext) if dot and len(ext) <= 10 else base[:MAX_FILENAME_CHARS]
    return base


def file_type_for(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in ALLOWED_TYPES:
        raise DocumentError(415, "unsupported_file_type", "Only .pdf, .md and .txt files are supported.")
    return ALLOWED_TYPES[suffix]


def _unsupported(msg: str) -> DocumentError:
    return DocumentError(415, "unsupported_file_type", msg)


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise _unsupported("The file is not a UTF-8 text file.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _unsupported("The file is not a UTF-8 text file.") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def validate_and_extract(filename: str, data: bytes) -> Extracted:
    """Check extension, size and content, then extract text with locations. Raises DocumentError."""
    file_type = file_type_for(filename)
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(413, "file_too_large", "The file is larger than 10 MB.")
    if not data:
        raise DocumentError(422, "empty_file", "The file is empty.")
    if file_type == "pdf":
        if not data.startswith(b"%PDF-"):
            raise _unsupported("The file does not look like a PDF.")
        return _extract_pdf(data)
    if data.lstrip().startswith(b"%PDF-"):
        raise _unsupported("The file looks like a PDF, not a text file.")
    text = _decode_text(data)
    if not text.strip():
        raise DocumentError(422, "empty_file", "The file contains no text.")
    if file_type == "md":
        return Extracted("md", _markdown_sections(text), None)
    return Extracted("txt", [Section("all", text, 1, {})], None)


def _extract_pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise DocumentError(422, "unreadable_pdf", "The PDF is password-protected.")
        sections = []
        pages = reader.pages
        for i, page in enumerate(pages, start=1):
            text = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
            if text.strip():
                sections.append(Section(f"p{i}", text, 0, {"page": i}))
        page_count = len(pages)
    except DocumentError:
        raise
    except Exception:
        raise DocumentError(422, "unreadable_pdf", "The PDF could not be read.") from None
    if not sections:
        raise DocumentError(422, "no_extractable_text", "The PDF has no text layer (scanned PDFs are not supported).")
    return Extracted("pdf", sections, page_count)


def _markdown_sections(text: str) -> list[Section]:
    """Split at ATX headings outside fenced code blocks; each section carries its heading path."""
    lines = text.split("\n")
    starts: list[tuple[int, str | None]] = [(0, None)]  # (line index, heading path)
    stack: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            starts.append((i, " > ".join(t for _, t in stack)))
    sections = []
    for n, (start, heading) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start:end])
        if body.strip():
            sections.append(Section(f"s{len(sections)}", body, start + 1, {"heading": heading}))
    return sections
