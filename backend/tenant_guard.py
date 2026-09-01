"""
backend/tenant_guard.py
=======================
Tenant scoping enforced at the session boundary.

Every ORM SELECT gets `WHERE company_id = <current tenant>` and every INSERT gets
`company_id` stamped, from the request-scoped tenant in `backend.tenancy`.

**Why here and not at the call sites.** There are around 375 `db.execute` sites in
this backend. Editing each one to add a filter would be error-prone, and — worse —
would leave the next query anyone writes unprotected by default. Scoping is a
property of the session, not of individual queries, so it is enforced once, where
it cannot be forgotten. This is the same reason `apply_company_scope` was changed
to raise rather than silently no-op: a guard you can accidentally omit is not a
guard.

**What is covered.** Every read, because every query in this backend goes through
the ORM — there is no raw SQL execution anywhere in `backend/`. Loader criteria
reach joined and aliased entities and relationship loads, so a filtered parent
cannot hand back unfiltered children.

**When no tenant is bound**, queries are unscoped. That is correct for seeding,
scripts and tests, which legitimately operate across companies. It is *not* left
to chance for API traffic: the request middleware binds a tenant on every request,
and `tests/test_tenancy_enforcement.py` asserts that it does. For deliberate
cross-tenant work inside a request, use `unscoped()` — so bypassing is always a
visible decision rather than an omission.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from backend.tenancy import get_current_tenant

#: Set while a deliberate cross-tenant operation is in progress.
_bypass: ContextVar[bool] = ContextVar("tenant_filter_bypass", default=False)

#: Execution option a single statement can carry to opt out, e.g.
#: `select(Rep).execution_options(skip_tenant_filter=True)`.
SKIP_OPTION = "skip_tenant_filter"


class TenantStampError(RuntimeError):
    """Raised when a tenant-scoped row would be written with no company."""


class _Unscoped:
    """
    Suspends tenant filtering for a block, in sync or async form.

    For genuine cross-tenant work — listing which companies exist, an admin
    report spanning tenants, the loader replacing one company's rows. Dual
    protocol for the same reason as `tenancy.tenant_scope`: it is usually opened
    alongside a session in an `async with`.
    """

    __slots__ = ("_token",)

    def __init__(self) -> None:
        self._token: Any = None

    def __enter__(self) -> None:
        self._token = _bypass.set(True)

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _bypass.reset(self._token)
            self._token = None

    async def __aenter__(self) -> None:
        self.__enter__()

    async def __aexit__(self, *exc: object) -> None:
        self.__exit__(*exc)


def unscoped() -> _Unscoped:
    """Run a block without tenant filtering. Keep it as small as the operation."""
    return _Unscoped()


def filtering_active() -> bool:
    """True when the next ORM select would be tenant-filtered."""
    return not _bypass.get() and get_current_tenant() is not None


def install(tenant_mixin: type) -> None:
    """
    Register the session listeners against the declarative mixin that carries
    `company_id`. Called once from `backend/models.py`, after the mixin exists.

    Passing the mixin in rather than importing it keeps this module free of a
    circular import with the models it protects.
    """

    @event.listens_for(Session, "do_orm_execute")
    def _scope_selects(state: Any) -> None:
        if not state.is_select or state.is_column_load:
            return
        if state.execution_options.get(SKIP_OPTION) or _bypass.get():
            return

        tenant = get_current_tenant()
        if tenant is None:
            return

        state.statement = state.statement.options(
            with_loader_criteria(
                tenant_mixin,
                lambda cls: cls.company_id == tenant,
                include_aliases=True,
            )
        )

    @event.listens_for(Session, "before_flush")
    def _stamp_inserts(session: Session, flush_context: Any, instances: Any) -> None:
        tenant = get_current_tenant()
        for obj in session.new:
            if not isinstance(obj, tenant_mixin):
                continue
            if getattr(obj, "company_id", None):
                continue  # explicitly set by the caller — leave it alone
            if tenant is None:
                if _bypass.get():
                    continue  # deliberate cross-tenant write, e.g. a backfill
                raise TenantStampError(
                    f"Refusing to write {type(obj).__name__} with no company_id and no "
                    "tenant bound. Bind one with tenancy.tenant_scope(...), set "
                    "company_id explicitly, or wrap the operation in "
                    "tenant_guard.unscoped() if it is genuinely cross-tenant."
                )
            obj.company_id = tenant
