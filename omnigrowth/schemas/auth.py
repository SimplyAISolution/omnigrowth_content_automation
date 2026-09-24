"""Auth API schemas (API_PLAN auth group)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from omnigrowth.core.enums import UserRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105  # nosec B105 - OAuth token type label
    expires_at: datetime


class MeRead(BaseModel):
    user_id: uuid.UUID
    email: str
    role: UserRole
