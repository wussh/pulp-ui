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
    }
)


def redact(record: dict) -> dict:
    return {
        key: value for key, value in record.items() if key.lower() not in REDACT_KEYS
    }


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
