from qa_shapes import is_session_id, is_timestamp_with_timezone


def test_CHAT_AC1_session_id_validator_checks_lowercase_and_length():
    assert is_session_id("0123456789abcdef" * 2)
    assert not is_session_id("0123456789ABCDEF" * 2)
    assert not is_session_id("g" * 32)
    assert not is_session_id("a" * 31)
    assert not is_session_id("a" * 33)
    assert not is_session_id(123)


def test_SESS_AC2_timestamp_validator_requires_timezone():
    assert is_timestamp_with_timezone("2026-11-16T12:34:56Z")
    assert is_timestamp_with_timezone("2026-11-16T12:34:56+05:30")
    assert not is_timestamp_with_timezone("2026-11-16T12:34:56")
    assert not is_timestamp_with_timezone("2026-02-31T12:34:56Z")
    assert not is_timestamp_with_timezone(None)
