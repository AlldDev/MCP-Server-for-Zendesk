import json
import logging

from mcp_zendesk.config import _JSONLogFormatter


def test_json_formatter_emits_valid_json_with_expected_fields():
    formatter = _JSONLogFormatter()
    record = logging.LogRecord(
        name="mcp_zendesk.client", level=logging.WARNING, pathname="", lineno=0, msg="rate limited", args=(), exc_info=None
    )
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "mcp_zendesk.client"
    assert payload["message"] == "rate limited"
    assert "exc_info" not in payload
