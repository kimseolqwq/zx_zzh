from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models import User


password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted_token: str | None) -> None:
    expected = request.session.get("csrf_token", "")
    if not submitted_token or not expected or not hmac.compare_digest(expected, submitted_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")


def current_admin(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if not user or not user.is_active or user.role != "admin":
        request.session.clear()
        return None
    return user


def require_admin(request: Request, db: Session) -> User:
    user = current_admin(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要管理员登录")
    return user


@dataclass
class LoginLimiter:
    max_attempts: int = 5
    window_seconds: int = 300

    def __post_init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _clean(self, key: str) -> deque[float]:
        now = time.monotonic()
        attempts = self._attempts[key]
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        return attempts

    def allowed(self, key: str) -> bool:
        return len(self._clean(key)) < self.max_attempts

    def failure(self, key: str) -> None:
        self._clean(key).append(time.monotonic())

    def success(self, key: str) -> None:
        self._attempts.pop(key, None)


login_limiter = LoginLimiter()

