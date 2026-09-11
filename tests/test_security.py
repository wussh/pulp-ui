from app.security import (
    check_credentials,
    hash_password,
    issue_csrf,
    validate_csrf,
    verify_password,
)


def test_password_round_trip():
    encoded = hash_password("correct horse battery staple", salt=b"0123456789abcdef")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong", encoded) is False


def test_password_hash_is_salted():
    first = hash_password("same-password", salt=b"0123456789abcdef")
    second = hash_password("same-password", salt=b"fedcba9876543210")
    assert first != second


def test_verify_password_rejects_malformed_encoding():
    assert verify_password("anything", "not-a-hash") is False
    assert verify_password("anything", "scrypt$bad$8$1$aa$bb") is False


def test_check_credentials_accepts_the_configured_operator(settings):
    assert check_credentials("operator", "s3cret", settings) is True


def test_check_credentials_rejects_wrong_user_or_password(settings):
    assert check_credentials("operator", "wrong", settings) is False
    assert check_credentials("nobody", "s3cret", settings) is False


def test_csrf_round_trip():
    nonce, token = issue_csrf("secret", nonce="abc123")
    assert nonce == "abc123"
    assert validate_csrf("secret", nonce, token) is True


def test_csrf_rejects_wrong_secret_nonce_and_garbage():
    nonce, token = issue_csrf("secret", nonce="abc123")
    assert validate_csrf("other-secret", nonce, token) is False
    assert validate_csrf("secret", "different", token) is False
    assert validate_csrf("secret", nonce, "garbage") is False
    assert validate_csrf("secret", None, None) is False
