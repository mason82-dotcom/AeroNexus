"""Web users: JWT of the DJI backend (HMAC256, shared JWT_SECRET). Agents: static bearer token."""
import secrets
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, Query

from .config import Settings


@dataclass(frozen=True)
class User:
    workspace_id: str
    username: str


def decode_user(settings: Settings, token: str | None) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Anmeldung fehlt")
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], issuer="DJI",
            options={"require": ["exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Anmeldung ung\u00fcltig oder abgelaufen") from exc
    workspace_id = claims.get("workspace_id")
    if not workspace_id:
        raise HTTPException(status_code=401, detail="Anmeldung ohne Arbeitsbereich")
    return User(workspace_id=workspace_id, username=str(claims.get("username", "")))


def user_dependency(settings: Settings):
    def dep(x_auth_token: str | None = Header(default=None)) -> User:
        return decode_user(settings, x_auth_token)
    return dep


def tile_user_dependency(settings: Settings):
    """Leaflet loads tiles via <img>, which cannot send headers: token comes as query parameter."""
    def dep(token: str | None = Query(default=None)) -> User:
        return decode_user(settings, token)
    return dep


def agent_dependency(settings: Settings):
    expected = f"Bearer {settings.agent_token}"

    def dep(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid agent token")
    return dep
