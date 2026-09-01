"""
backend/auth/tokens.py
======================
JWT verification for production-mode authentication.

This module is the single place a bearer token is turned into trusted claims.
Nothing else in the codebase may read identity from a request — a header the
caller controls is an assertion, not an authentication.

The token is verified for signature, expiry, not-before, and (when configured)
issuer and audience. A token that fails any of those is rejected with 401; a
missing or malformed server secret is rejected with 500, because silently
accepting unverified tokens is how the previous scaffold turned into an
authorization bypass.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from backend.config import settings

try:
    import jwt
    from jwt import (
        ExpiredSignatureError,
        ImmatureSignatureError,
        InvalidAudienceError,
        InvalidIssuerError,
        InvalidTokenError,
    )

    PYJWT_AVAILABLE = True
    PYJWT_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - environment-specific
    jwt = None
    ExpiredSignatureError = ImmatureSignatureError = InvalidTokenError = Exception
    InvalidAudienceError = InvalidIssuerError = Exception
    PYJWT_AVAILABLE = False
    PYJWT_IMPORT_ERROR = str(exc)


#: Claims that may appear in a token and the UserContext field each maps to.
CLAIM_TO_FIELD: dict[str, str] = {
    "sub": "user_id",
    "role": "role",
    "company_id": "company_id",
    "team_id": "team_id",
    "territory_id": "territory_id",
}


def auth_is_configured() -> bool:
    """True when production-mode authentication can actually be performed."""
    return bool(settings.AUTH_JWT_SECRET) and PYJWT_AVAILABLE


def assert_auth_configured() -> None:
    """
    Fail loudly when production mode is on but authentication cannot work.

    Called at application startup so a misconfigured deployment refuses to boot
    rather than serving unauthenticated traffic.
    """
    if settings.DEMO_MODE:
        return
    if not PYJWT_AVAILABLE:
        raise RuntimeError(
            "DEMO_MODE=false requires PyJWT for token verification, but importing it "
            f"failed: {PYJWT_IMPORT_ERROR}. Install it with `pip install -r requirements.txt`."
        )
    if not settings.AUTH_JWT_SECRET:
        raise RuntimeError(
            "DEMO_MODE=false requires AUTH_JWT_SECRET to be set. Refusing to start: "
            "without a secret, no token can be verified and every request would be "
            "trusted on its own say-so."
        )


def decode_token(token: str) -> dict[str, Any]:
    """
    Verify `token` and return its claims.

    Raises HTTPException(401) for anything wrong with the token itself, and
    HTTPException(500) when the server is not configured to verify tokens at
    all — those are different failures and must not be conflated.
    """
    if not PYJWT_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="Server cannot verify tokens: PyJWT is not installed.",
        )
    if not settings.AUTH_JWT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Server cannot verify tokens: AUTH_JWT_SECRET is not configured.",
        )

    options = {
        "require": ["exp"],
        "verify_signature": True,
        "verify_exp": True,
        "verify_aud": bool(settings.AUTH_JWT_AUDIENCE),
        "verify_iss": bool(settings.AUTH_JWT_ISSUER),
    }

    try:
        claims = jwt.decode(
            token,
            settings.AUTH_JWT_SECRET,
            algorithms=[settings.AUTH_JWT_ALGORITHM],
            leeway=settings.AUTH_JWT_LEEWAY_SECONDS,
            audience=settings.AUTH_JWT_AUDIENCE or None,
            issuer=settings.AUTH_JWT_ISSUER or None,
            options=options,
        )
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token has expired.") from exc
    except ImmatureSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token is not yet valid.") from exc
    except InvalidAudienceError as exc:
        raise HTTPException(status_code=401, detail="Token audience is not accepted.") from exc
    except InvalidIssuerError as exc:
        raise HTTPException(status_code=401, detail="Token issuer is not accepted.") from exc
    except InvalidTokenError as exc:
        # Covers bad signature, malformed segments, wrong algorithm, missing exp.
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="Invalid token payload.")
    return claims


def claims_to_context_fields(claims: dict[str, Any]) -> dict[str, str]:
    """Project verified claims onto UserContext field names, dropping blanks."""
    fields: dict[str, str] = {}
    for claim, field_name in CLAIM_TO_FIELD.items():
        value = claims.get(claim)
        if isinstance(value, str) and value.strip():
            fields[field_name] = value.strip()
    return fields


def issue_token(
    *,
    user_id: str,
    role: str,
    company_id: str | None = None,
    team_id: str | None = None,
    territory_id: str | None = None,
    expires_in_seconds: int = 3600,
) -> str:
    """
    Mint a token with the current settings — for local development, tests, and
    the demo walkthrough. A real deployment gets tokens from its identity
    provider; this exists so the verification path can be exercised without one.
    """
    import time

    if not PYJWT_AVAILABLE:
        raise RuntimeError("PyJWT is required to issue tokens.")
    if not settings.AUTH_JWT_SECRET:
        raise RuntimeError("AUTH_JWT_SECRET must be set to issue tokens.")

    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "nbf": now,
        "exp": now + expires_in_seconds,
    }
    if company_id:
        payload["company_id"] = company_id
    if team_id:
        payload["team_id"] = team_id
    if territory_id:
        payload["territory_id"] = territory_id
    if settings.AUTH_JWT_ISSUER:
        payload["iss"] = settings.AUTH_JWT_ISSUER
    if settings.AUTH_JWT_AUDIENCE:
        payload["aud"] = settings.AUTH_JWT_AUDIENCE

    return jwt.encode(payload, settings.AUTH_JWT_SECRET, algorithm=settings.AUTH_JWT_ALGORITHM)
