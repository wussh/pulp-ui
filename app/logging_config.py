import logging

REDACT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "token",
        "secret",
        "body",
        "json",
        "content",
        "pulp_admin_password",
        "ui_password_hash",
        "session_secret",
        "json_body",
    }
)


def _redact_value(value):
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact(record: dict) -> dict:
    return {
        key: _redact_value(value)
        for key, value in record.items()
        if key.lower() not in REDACT_KEYS
    }


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
