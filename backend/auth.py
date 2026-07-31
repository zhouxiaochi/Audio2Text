"""Authentication helpers for username/password sessions."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from pwdlib import PasswordHash

from backend.config import Settings
from backend.db import JobStore

COOKIE_NAME = "audio2text_session"
password_hash = PasswordHash.recommended()


def create_session(store: JobStore, user_id: str, settings: Settings) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    store.create_session(user_id, token, expires_at)
    return token, expires_at


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    user = request.app.state.store.get_session_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录。")
    return user


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限。")
    return user
