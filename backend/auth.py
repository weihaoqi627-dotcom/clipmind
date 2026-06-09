"""
JWT 认证 + 密码哈希
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from .config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

# ── 密码哈希 ──
# 用 hashlib.pbkdf2_hmac 代替 passlib（passlib 在 Python 3.14 有兼容问题）
# 对 MVP 来说足够安全。后续可以切 argon2。

_SALT_LENGTH = 16
_HASH_ITERATIONS = 600_000
_HASH_ALGO = "sha256"


def hash_password(password: str) -> str:
    """返回 salt$hash 格式的字符串"""
    salt = secrets.token_hex(_SALT_LENGTH)
    pwd_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("ascii")
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, pwd_bytes, salt_bytes, _HASH_ITERATIONS)
    return f"{salt}${dk.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        salt, stored_hash = hashed.split("$", 1)
    except ValueError:
        return False
    pwd_bytes = plain.encode("utf-8")
    salt_bytes = salt.encode("ascii")
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, pwd_bytes, salt_bytes, _HASH_ITERATIONS)
    return dk.hex() == stored_hash


# ── JWT ──


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """返回 user_id，无效返回 None"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None
