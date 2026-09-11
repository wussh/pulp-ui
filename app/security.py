import hashlib
import hmac
import secrets

from app.config import Settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_BYTES = 16


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, expected_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(expected_hex)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


def check_credentials(username: str, password: str, settings: Settings) -> bool:
    username_ok = hmac.compare_digest(username, settings.ui_username)
    password_ok = verify_password(password, settings.ui_password_hash)
    return username_ok & password_ok


def issue_csrf(secret: str, nonce: str | None = None) -> tuple[str, str]:
    nonce = nonce or secrets.token_urlsafe(24)
    signature = hmac.new(
        secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return nonce, f"{nonce}.{signature}"


def validate_csrf(secret: str, nonce: str | None, token: str | None) -> bool:
    if not nonce or not token:
        return False
    try:
        token_nonce, signature = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(token_nonce, nonce):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
