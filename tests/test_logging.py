import logging

from labmeeting.logging_setup import redact, setup_logging


# 11. API 키가 로그에 노출되지 않음
def test_secrets_never_written_to_log(tmp_path):
    secrets = ["sk-proj-SuperSecretOpenAIKey1234567890", "ntn_SecretNotionToken12345678",
               "my-custom-key-without-prefix-999"]
    logger = setup_logging(tmp_path, secrets)
    logger.info("openai key=%s", secrets[0])
    logger.warning("headers: {'Authorization': 'Bearer %s'}", secrets[1])
    logger.error("custom %s and anthropic sk-ant-api03-abcdefghijklmnop", secrets[2])
    try:
        raise RuntimeError(f"failed with {secrets[0]}")
    except RuntimeError:
        logger.exception("boom")
    for h in logger.handlers:
        h.flush()
    content = (tmp_path / "app.log").read_text(encoding="utf-8")
    for s in secrets + ["sk-ant-api03-abcdefghijklmnop"]:
        assert s not in content
    assert "[REDACTED]" in content and "boom" in content


def test_redact_patterns():
    assert "sk-" not in redact("key sk-abcdefghijklmnop")
    assert redact("Bearer abc.def-ghi") == "Bearer [REDACTED]"


def test_notion_client_errors_do_not_include_token(fake_notion):
    import pytest

    from labmeeting.notion_client import NotionClient, NotionError

    token = "ntn_ThisIsTheSecretToken000000"
    client = NotionClient(token, transport=fake_notion.transport, sleep=lambda s: None, min_interval=0)
    fake_notion.fail("GET", r"^/databases/", status=401, code="unauthorized")
    with pytest.raises(NotionError) as exc:
        client.get_database("11111111-1111-1111-1111-111111111111")
    assert token not in str(exc.value)
    logging.getLogger("labmeeting").handlers.clear()


def test_db_id_error_never_echoes_secret():
    import pytest

    from labmeeting.notion_client import NotionError, parse_notion_id

    for secret in ("ntn_238877943931AbCdEfGhIjKlMnOp", "sk-proj-AbCdEfGhIjKlMnOpQrStUv"):
        with pytest.raises(NotionError) as exc:
            parse_notion_id(secret)
        assert secret not in str(exc.value) and "키" in str(exc.value)
