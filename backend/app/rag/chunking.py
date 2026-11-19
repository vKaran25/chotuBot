"""Deterministic chunking with stable ids (P2-REQ-4). Same input + same index version => same chunks and ids."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.rag.config import CHUNK_OVERLAP, CHUNK_SIZE, INDEX_VERSION
from app.rag.extract import Extracted, Section

_SENTENCE_END = re.compile(r"[.!?](?=\s)")


@dataclass(frozen=True)
class Chunk:
    id: str
    ordinal: int
    section_key: str
    start: int  # offset in the section text
    end: int
    text: str
    location: dict[str, Any]


def chunk_id(document_id: str, section_key: str, start: int, version: str = INDEX_VERSION) -> str:
    return hashlib.sha256(f"{document_id}:{version}:{section_key}:{start}".encode()).hexdigest()[:32]


def _break_point(text: str, pos: int, limit: int, size: int) -> int:
    """Best end for a window starting at pos: paragraph > sentence > line > word boundary > hard cut."""
    floor = pos + size // 2
    i = text.rfind("\n\n", floor, limit)
    if i != -1:
        return i
    last = None
    for m in _SENTENCE_END.finditer(text, floor, limit):
        last = m
    if last is not None:
        return last.end()
    for sep in ("\n", " ", "\t"):
        i = text.rfind(sep, floor, limit)
        if i != -1:
            return i
    return limit


def windows(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """Return (start, end) spans of at most `size` characters, stripped of outer whitespace."""
    n = len(text)
    spans: list[tuple[int, int]] = []
    pos = 0
    while pos < n:
        end = n if pos + size >= n else _break_point(text, pos, pos + size, size)
        s, e = pos, end
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            spans.append((s, e))
        if end >= n:
            break
        nxt = end - overlap
        if nxt <= pos:
            nxt = end
        else:
            while nxt < end and not text[nxt - 1].isspace():  # start the overlap on a word boundary
                nxt += 1
        pos = nxt
    return spans


def _location(section: Section, file_type: str, s: int, e: int) -> dict[str, Any]:
    if file_type == "pdf":
        return dict(section.location)
    first = section.base_line + section.text.count("\n", 0, s)
    last = section.base_line + section.text.count("\n", 0, max(s, e - 1))
    loc: dict[str, Any] = {"line_start": first, "line_end": last}
    if file_type == "md":
        loc = {"heading": section.location.get("heading"), **loc}
    return loc


def chunk_document(document_id: str, doc: Extracted, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        for s, e in windows(section.text, size, overlap):
            chunks.append(
                Chunk(
                    id=chunk_id(document_id, section.key, s),
                    ordinal=len(chunks),
                    section_key=section.key,
                    start=s,
                    end=e,
                    text=section.text[s:e],
                    location=_location(section, doc.file_type, s, e),
                )
            )
    return chunks
