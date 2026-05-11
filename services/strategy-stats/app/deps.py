"""FastAPI dependencies: Basic Auth + DB session."""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.db import get_session  # re-export for routers
from app.settings import get_settings

__all__ = ["verify_basic_auth", "get_session"]

_security = HTTPBasic()


def verify_basic_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> str:
    settings = get_settings()
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.basic_auth_user.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.basic_auth_password.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
