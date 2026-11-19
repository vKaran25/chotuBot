"""P2-T32 privacy checks: excerpts never reach traces or logs; stored files never use the uploaded name."""
from __future__ import annotations

import logging

from app.security.redaction import redact_for_trace
from tests.test_rag_api import MD, client_for, upload

SECRET_LINE = "Night temperatures dropped to minus 20 degrees Celsius."


def test_trace_redaction_hides_document_excerpts():
    payload = {"messages": [[{"type": "human", "content": f"<excerpts>\n[1] (notes.md)\n{SECRET_LINE}\n</excerpts>"}]]}
    out = repr(redact_for_trace(payload))
    assert "minus 20" not in out and "notes.md" not in out


def test_no_document_text_in_logs(tmp_db, caplog):
    caplog.set_level(logging.DEBUG)
    c, _ = client_for(tmp_db, "demo")
    upload(c, MD)
    c.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True})
    c.post("/api/documents", files={"file": ("bad.pdf", b"%PDF-1.4 broken " + SECRET_LINE.encode(), "application/pdf")})
    assert "minus 20" not in caplog.text and "Ladakh" not in caplog.text


def test_stored_original_uses_generated_name_only(tmp_db):
    c, _ = client_for(tmp_db, "demo")
    doc = upload(c, MD).json()
    files = list((tmp_db.parent / f"{tmp_db.stem}.rag" / "files").rglob("*"))
    names = [p.name for p in files if p.is_file()]
    assert names == [f"{doc['id']}.md"]
