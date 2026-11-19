"""P2-T11/T13: supported formats, text extraction with locations, deterministic chunking and stable ids."""
from __future__ import annotations

import pytest

from app.config import REPO_ROOT
from app.rag.chunking import chunk_document, chunk_id, windows
from app.rag.errors import DocumentError
from app.rag.extract import clean_filename, validate_and_extract

FX = REPO_ROOT / "evals" / "fixtures" / "phase2"


def read(name: str) -> bytes:
    return (FX / name).read_bytes()


def code_of(exc_info) -> tuple[int, str]:
    return exc_info.value.status_code, exc_info.value.code


# ---- extraction ----------------------------------------------------------
def test_pdf_pages_and_text():
    doc = validate_and_extract("orion-handbook.pdf", read("orion-handbook.pdf"))
    assert doc.file_type == "pdf" and doc.page_count == 2
    assert [s.location for s in doc.sections] == [{"page": 1}, {"page": 2}]
    assert "battery lasts 14 hours" in doc.sections[0].text
    assert "wheel bearings every 400 kilometres" in doc.sections[1].text


def test_markdown_sections_have_heading_paths_and_skip_code_fences():
    doc = validate_and_extract("orion-field-notes.md", read("orion-field-notes.md"))
    headings = [s.location["heading"] for s in doc.sections]
    assert headings == ["Orion field notes", "Orion field notes > Ladakh trip",
                        "Orion field notes > Lessons", "Orion field notes > Next steps"]
    ladakh = doc.sections[1]
    assert ladakh.base_line == 5 and "minus 20 degrees" in ladakh.text
    assert "not a heading" in doc.sections[2].text  # the fenced '# ...' line stayed in its section


def test_txt_is_one_section_from_line_1():
    doc = validate_and_extract("kestrel-budget.txt", read("kestrel-budget.txt"))
    assert doc.file_type == "txt" and doc.page_count is None
    [s] = doc.sections
    assert s.base_line == 1 and "42 lakh rupees" in s.text


def test_bom_and_crlf_are_normalised():
    doc = validate_and_extract("a.txt", "\ufeffline one\r\nline two\r".encode("utf-8"))
    assert doc.sections[0].text == "line one\nline two\n"


@pytest.mark.parametrize("name", ["a.docx", "a.html", "a.exe", "noext", "a.pdf.exe", "a.markdown"])
def test_disallowed_extensions(name):
    with pytest.raises(DocumentError) as e:
        validate_and_extract(name, b"hello")
    assert code_of(e) == (415, "unsupported_file_type")


@pytest.mark.parametrize("name,data", [
    ("fake.pdf", b"just some text"),
    ("x.txt", b"%PDF-1.4 binary"),
    ("x.txt", b"abc\x00def"),
    ("x.md", b"\xff\xfe\xfa bad utf8"),
])
def test_content_must_match_extension(name, data):
    with pytest.raises(DocumentError) as e:
        validate_and_extract(name, data)
    assert code_of(e) == (415, "unsupported_file_type")


@pytest.mark.parametrize("name,data", [("e.txt", b""), ("e.md", b"  \n\t\n"), ("e.pdf", b"")])
def test_empty_files(name, data):
    with pytest.raises(DocumentError) as e:
        validate_and_extract(name, data)
    assert code_of(e) == (422, "empty_file")


def test_pdf_without_text_layer():
    with pytest.raises(DocumentError) as e:
        validate_and_extract("scan.pdf", read("no-text.pdf"))
    assert code_of(e) == (422, "no_extractable_text")


def test_corrupt_pdf():
    with pytest.raises(DocumentError) as e:
        validate_and_extract("bad.pdf", read("corrupt.pdf"))
    assert code_of(e) == (422, "unreadable_pdf")


def test_too_large():
    with pytest.raises(DocumentError) as e:
        validate_and_extract("big.txt", b"a" * (10 * 1024 * 1024 + 1))
    assert code_of(e) == (413, "file_too_large")


@pytest.mark.parametrize("raw,expected", [
    ("../../evil.txt", "evil.txt"), ("C:\\x\\y.md", "y.md"), ("  notes.md  ", "notes.md"),
    ("bad\x00\x07name.txt", "badname.txt"), ("a" * 300 + ".txt", "a" * 196 + ".txt"),
])
def test_clean_filename(raw, expected):
    assert clean_filename(raw) == expected


# ---- chunking ------------------------------------------------------------
def test_windows_respect_size_and_overlap():
    para = "Sentence number {i} talks about the rover battery and its charge. "
    text = "\n\n".join("".join(para.format(i=i * 10 + j) for j in range(5)) for i in range(12))
    spans = windows(text, 1000, 150)
    assert len(spans) > 3
    assert all(e - s <= 1000 for s, e in spans)
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert s2 < e1, "consecutive chunks overlap"
        assert e1 - s2 <= 150
    assert spans[0][0] == 0 and spans[-1][1] == len(text.rstrip())


def test_windows_never_split_inside_a_word_when_spaces_exist():
    text = " ".join(f"word{i:04d}" for i in range(600))
    for s, e in windows(text, 1000, 150):
        assert text[s:e].split()[0].startswith("word") and len(text[s:e].split()[0]) == 8
        assert len(text[s:e].split()[-1]) == 8


def test_hard_cut_without_any_whitespace():
    spans = windows("x" * 2500, 1000, 150)
    assert spans[0] == (0, 1000) and all(e - s <= 1000 for s, e in spans)
    assert spans[-1][1] == 2500


def test_chunking_is_deterministic_and_ids_stable():
    data = read("orion-field-notes.md")
    a = chunk_document("doc1", validate_and_extract("n.md", data))
    b = chunk_document("doc1", validate_and_extract("n.md", data))
    assert a == b
    assert all(len(c.id) == 32 for c in a)
    assert len({c.id for c in a}) == len(a)
    assert a[0].id == chunk_id("doc1", a[0].section_key, a[0].start)
    other = chunk_document("doc2", validate_and_extract("n.md", data))
    assert {c.id for c in a}.isdisjoint({c.id for c in other})


def test_chunk_locations_resolve_to_source():
    md = chunk_document("d", validate_and_extract("n.md", read("orion-field-notes.md")))
    src_lines = read("orion-field-notes.md").decode().split("\n")
    for c in md:
        loc = c.location
        assert set(loc) == {"heading", "line_start", "line_end"}
        quoted = "\n".join(src_lines[loc["line_start"] - 1: loc["line_end"]])
        assert c.text in quoted
    pdf = chunk_document("p", validate_and_extract("h.pdf", read("orion-handbook.pdf")))
    assert [c.location for c in pdf] == [{"page": 1}, {"page": 2}]
    txt = chunk_document("t", validate_and_extract("b.txt", read("kestrel-budget.txt")))
    assert txt[0].location["line_start"] == 1 and set(txt[0].location) == {"line_start", "line_end"}


def test_chunks_do_not_cross_pdf_pages_or_md_sections():
    md = chunk_document("d", validate_and_extract("n.md", read("orion-field-notes.md")))
    assert len({c.section_key for c in md}) == 4
    assert not any("Ladakh" in c.text and "Rajasthan" in c.text for c in md)
