from dayboard.security.redaction import redact, safe_text


def test_sensitive_keys_are_redacted_recursively():
    result = redact(
        {
            "status": "ok",
            "access_token": "SYNTHETIC_SECRET",
            "nested": [{"Password": "SYNTHETIC_SECRET", "count": 2}],
            "Authorization": "Bearer SYNTHETIC_SECRET",
            "client_secret": "SYNTHETIC_SECRET",
        }
    )
    assert "SYNTHETIC_SECRET" not in str(result)
    assert result["status"] == "ok"
    assert result["nested"][0]["count"] == 2


def test_text_removes_known_secrets_and_terminal_controls():
    text = "\x1b[31mFailed Bearer SYNTHETIC_TOKEN password=FAKE_PASSWORD\x1b[0m"
    result = safe_text(text)
    assert "SYNTHETIC_TOKEN" not in result
    assert "FAKE_PASSWORD" not in result
    assert "\x1b" not in result
    assert "Failed" in result


def test_plain_text_and_numbers_are_preserved():
    assert redact({"cpu": 12, "state": "not connected"}) == {
        "cpu": 12,
        "state": "not connected",
    }


def test_known_secret_in_arbitrary_message_is_removed():
    assert safe_text("backend echoed FAKE_PRIVATE_VALUE", ("FAKE_PRIVATE_VALUE",)) == (
        "backend echoed [REDACTED]"
    )
