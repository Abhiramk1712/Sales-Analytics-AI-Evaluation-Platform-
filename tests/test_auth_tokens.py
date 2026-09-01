"""
tests/test_auth_tokens.py
=========================
Token verification: the boundary between an assertion and an authentication.

Everything here exists because the previous implementation had no boundary at
all — `Bearer demo:role=revops_admin` was parsed by splitting on semicolons and
believed. These tests pin the properties that make the new path meaningful:
a bad signature, a wrong secret, an expired token and a missing secret must all
fail, and they must fail differently enough to be debuggable.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from backend.auth.dependencies import get_user_context
from backend.auth.tokens import decode_token, issue_token
from backend.config import settings

SECRET = "test-secret-not-for-production"
OTHER_SECRET = "a-different-secret-entirely"


@pytest.fixture
def production_auth():
    """Production mode with a configured signing secret."""
    original = (settings.DEMO_MODE, settings.AUTH_JWT_SECRET,
                settings.AUTH_JWT_ISSUER, settings.AUTH_JWT_AUDIENCE)
    settings.DEMO_MODE = False
    settings.AUTH_JWT_SECRET = SECRET
    settings.AUTH_JWT_ISSUER = ""
    settings.AUTH_JWT_AUDIENCE = ""
    yield
    (settings.DEMO_MODE, settings.AUTH_JWT_SECRET,
     settings.AUTH_JWT_ISSUER, settings.AUTH_JWT_AUDIENCE) = original


# ── The happy path ───────────────────────────────────────────────────────────


def test_valid_token_round_trips(production_auth):
    token = issue_token(user_id="u-1", role="finance_admin", company_id="techo-solutions")
    claims = decode_token(token)

    assert claims["sub"] == "u-1"
    assert claims["role"] == "finance_admin"
    assert claims["company_id"] == "techo-solutions"


def test_valid_token_builds_the_user_context(production_auth):
    token = issue_token(
        user_id="u-2", role="revops_admin", company_id="insurex", team_id="t-9"
    )
    ctx = get_user_context(authorization=f"Bearer {token}")

    assert ctx.user_id == "u-2"
    assert ctx.role == "revops_admin"
    assert ctx.company_id == "insurex"
    assert ctx.team_id == "t-9"
    assert ctx.is_demo is False
    assert ctx.auth_source == "token"
    assert "approve_payouts" in ctx.permissions


# ── The failures that matter ─────────────────────────────────────────────────


def test_token_signed_with_another_secret_is_rejected(production_auth):
    settings.AUTH_JWT_SECRET = OTHER_SECRET
    forged = issue_token(user_id="attacker", role="revops_admin")
    settings.AUTH_JWT_SECRET = SECRET

    with pytest.raises(HTTPException) as exc:
        decode_token(forged)
    assert exc.value.status_code == 401


def test_tampered_payload_is_rejected(production_auth):
    token = issue_token(user_id="u-1", role="sales_rep")
    header, payload, signature = token.split(".")
    # Keep the signature, swap the payload for another valid-looking one.
    other = issue_token(user_id="u-1", role="revops_admin")
    _, other_payload, _ = other.split(".")

    with pytest.raises(HTTPException) as exc:
        decode_token(f"{header}.{other_payload}.{signature}")
    assert exc.value.status_code == 401


def test_expired_token_is_rejected(production_auth):
    token = issue_token(user_id="u-1", role="executive", expires_in_seconds=-3600)
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


def test_garbage_token_is_rejected(production_auth):
    with pytest.raises(HTTPException) as exc:
        decode_token("not-a-token-at-all")
    assert exc.value.status_code == 401


def test_token_without_a_role_claim_is_rejected(production_auth):
    import jwt

    now = int(time.time())
    token = jwt.encode(
        {"sub": "u-1", "iat": now, "exp": now + 600},
        SECRET,
        algorithm=settings.AUTH_JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        get_user_context(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


def test_unknown_role_claim_is_refused(production_auth):
    token = issue_token(user_id="u-1", role="superuser")
    with pytest.raises(HTTPException) as exc:
        get_user_context(authorization=f"Bearer {token}")
    assert exc.value.status_code == 403


def test_missing_secret_is_a_server_error_not_a_free_pass(production_auth):
    """
    An unconfigured secret must never degrade into accepting anything. 500 is
    correct here: the client did nothing wrong, the deployment did.
    """
    token = issue_token(user_id="u-1", role="executive")
    settings.AUTH_JWT_SECRET = ""

    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 500


# ── Issuer and audience, when configured ─────────────────────────────────────


def test_issuer_is_verified_when_configured(production_auth):
    settings.AUTH_JWT_ISSUER = "https://issuer.example"
    token = issue_token(user_id="u-1", role="executive")
    assert decode_token(token)["iss"] == "https://issuer.example"

    settings.AUTH_JWT_ISSUER = "https://somewhere-else.example"
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401


def test_audience_is_verified_when_configured(production_auth):
    settings.AUTH_JWT_AUDIENCE = "sales-analytics"
    token = issue_token(user_id="u-1", role="executive")
    assert decode_token(token)["aud"] == "sales-analytics"

    settings.AUTH_JWT_AUDIENCE = "a-different-service"
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401


# ── Demo mode is unchanged ───────────────────────────────────────────────────


def test_demo_mode_still_uses_headers():
    original = (settings.DEMO_MODE, settings.DEMO_DEFAULT_ROLE)
    try:
        settings.DEMO_MODE = True
        settings.DEMO_DEFAULT_ROLE = "executive"
        ctx = get_user_context(x_user_role="data_scientist", x_company_id="insurex")
        assert ctx.role == "data_scientist"
        assert ctx.company_id == "insurex"
        assert ctx.is_demo is True
    finally:
        settings.DEMO_MODE, settings.DEMO_DEFAULT_ROLE = original
