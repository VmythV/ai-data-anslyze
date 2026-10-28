"""凭据加密与登录凭证。两者都由同一个服务端密钥派生，密钥丢了已存的密码就解不开。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from cryptography.fernet import Fernet, InvalidToken

SESSION_COOKIE = "da_session"
SESSION_TTL_SECONDS = 14 * 24 * 3600


class SecretBox:
    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(f"secret-box:{secret}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        self._sign_key = hashlib.sha256(f"session:{secret}".encode()).digest()

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode() if plain else ""

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("凭据无法解密：服务端密钥可能已更换，请重新填写") from exc

    def issue_session(self, now: float | None = None) -> str:
        expires = int((now or time.time()) + SESSION_TTL_SECONDS)
        return f"{expires}.{self._sign(str(expires))}"

    def verify_session(self, token: str | None, now: float | None = None) -> bool:
        if not token or "." not in token:
            return False
        expires, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, self._sign(expires)):
            return False
        return expires.isdigit() and int(expires) > (now or time.time())

    def _sign(self, payload: str) -> str:
        return hmac.new(self._sign_key, payload.encode(), hashlib.sha256).hexdigest()


def mask(secret: str) -> str:
    """页面回显用：只露末四位。"""
    return "" if not secret else "••••" + secret[-4:]
