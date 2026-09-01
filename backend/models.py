"""
models.py — SQLAlchemy ORM models
"""
import uuid
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Numeric, Integer, SmallInteger, ForeignKey, Text, DateTime, Date, Boolean, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


class TenantScoped:
    """
    Marks a table as belonging to one company.

    Nullable for now: the column is introduced ahead of the query migration, so
    existing rows and the CSV loader keep working while call sites are moved
    over to `apply_company_scope` one at a time. It becomes NOT NULL once every
    write path populates it — tightening it before then would just break loads
    without making anything safer.
    """
    company_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)


class Team(Base, TenantScoped):
    __tablename__ = "teams"
    id:         Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:       Mapped[str]              = mapped_column(String(100))
    region:     Mapped[Optional[str]]    = mapped_column(String(50))
    created_at: Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)
    reps:       Mapped[list["Rep"]]      = relationship(back_populates="team")


class Rep(Base, TenantScoped):
    __tablename__ = "reps"
    id:         Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id:    Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("teams.id"))
    name:       Mapped[str]              = mapped_column(String(100))
    email:      Mapped[str]              = mapped_column(String(150))
    region:     Mapped[Optional[str]]    = mapped_column(String(50))
    hire_date:  Mapped[Optional[date]]   = mapped_column(Date)
    created_at: Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)
    team:       Mapped[Optional[Team]]   = relationship(back_populates="reps")
    deals:      Mapped[list["Deal"]]     = relationship(back_populates="rep")
    quotas:     Mapped[list["Quota"]]    = relationship(back_populates="rep")
    revenues:   Mapped[list["Revenue"]]  = relationship(back_populates="rep")

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (email). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("email", "company_id", name="uq_reps_email_company"),
    )


class Quota(Base, TenantScoped):
    __tablename__ = "quotas"
    id:         Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id:     Mapped[uuid.UUID]    = mapped_column(ForeignKey("reps.id"))
    period:     Mapped[str]          = mapped_column(String(20))
    amount:     Mapped[float]        = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)
    rep:        Mapped[Rep]          = relationship(back_populates="quotas")


class Account(Base, TenantScoped):
    __tablename__ = "accounts"
    id:             Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:           Mapped[str]             = mapped_column(String(150))
    industry:       Mapped[Optional[str]]   = mapped_column(String(80))
    employee_count: Mapped[Optional[int]]   = mapped_column(Integer)
    annual_revenue: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    country:        Mapped[str]             = mapped_column(String(80), default="United States")
    created_at:     Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)
    deals:          Mapped[list["Deal"]]    = relationship(back_populates="account")


class Deal(Base, TenantScoped):
    __tablename__ = "deals"
    id:                  Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"))
    rep_id:              Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"))
    name:                Mapped[str]             = mapped_column(String(200))
    product:             Mapped[Optional[str]]   = mapped_column(String(100))
    stage:               Mapped[str]             = mapped_column(String(50))
    amount:              Mapped[float]            = mapped_column(Numeric(14, 2), default=0)
    close_probability:   Mapped[Optional[int]]   = mapped_column(SmallInteger)
    expected_close_date: Mapped[Optional[date]]  = mapped_column(Date)
    actual_close_date:   Mapped[Optional[date]]  = mapped_column(Date)
    created_at:          Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:          Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    rep:         Mapped[Optional[Rep]]      = relationship(back_populates="deals")
    account:     Mapped[Optional[Account]]  = relationship(back_populates="deals")
    activities:  Mapped[list["Activity"]]   = relationship(back_populates="deal")


class Activity(Base, TenantScoped):
    __tablename__ = "activities"
    id:            Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id:       Mapped[uuid.UUID]       = mapped_column(ForeignKey("deals.id"))
    rep_id:        Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"))
    type:          Mapped[Optional[str]]   = mapped_column(String(50))
    outcome:       Mapped[Optional[str]]   = mapped_column(String(50))
    notes:         Mapped[Optional[str]]   = mapped_column(Text)
    activity_date: Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)
    deal:          Mapped[Deal]            = relationship(back_populates="activities")


class Revenue(Base, TenantScoped):
    __tablename__ = "revenue"
    id:          Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id:      Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"))
    period:      Mapped[str]          = mapped_column(String(20))
    amount:      Mapped[float]        = mapped_column(Numeric(14, 2), default=0)
    recorded_at: Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)
    # ── SaaS / RevOps fields (nullable so existing rows are unaffected) ───
    account_id:              Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    deal_id:                 Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("deals.id"),   nullable=True)
    revenue_type:            Mapped[Optional[str]]       = mapped_column(String(40),  nullable=True)   # new_biz | renewal | expansion | contraction | churn
    contract_term_months:    Mapped[Optional[int]]       = mapped_column(Integer,     nullable=True)
    recognition_start_date:  Mapped[Optional[date]]      = mapped_column(Date,        nullable=True)
    product_sku:             Mapped[Optional[str]]       = mapped_column(String(80),  nullable=True)
    is_recurring:            Mapped[Optional[bool]]      = mapped_column(Boolean,     nullable=True)
    rep:         Mapped[Optional[Rep]] = relationship(back_populates="revenues")


class MLPrediction(Base, TenantScoped):
    __tablename__ = "ml_predictions"
    id:            Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name:    Mapped[str]             = mapped_column(String(100))
    entity_type:   Mapped[Optional[str]]   = mapped_column(String(50))
    entity_id:     Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    prediction:    Mapped[dict]            = mapped_column(JSONB)
    confidence:    Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    model_version: Mapped[Optional[str]]   = mapped_column(String(20))
    predicted_at:  Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)


class ModelRunRecord(Base, TenantScoped):
    __tablename__ = "model_runs"
    id:            Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name:    Mapped[str]             = mapped_column(String(100))
    model_version: Mapped[Optional[str]]   = mapped_column(String(40))
    trained_at:    Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)
    training_rows: Mapped[Optional[int]]   = mapped_column(Integer)
    feature_names: Mapped[Optional[list]]  = mapped_column(JSONB)
    target_name:   Mapped[Optional[str]]   = mapped_column(String(100))
    metrics:       Mapped[Optional[dict]]  = mapped_column(JSONB)
    limitations:   Mapped[Optional[list]]  = mapped_column(JSONB)
    artifact_path: Mapped[Optional[str]]   = mapped_column(String(255))
    data_hash:     Mapped[Optional[str]]   = mapped_column(String(64))
    notes:         Mapped[Optional[str]]   = mapped_column(Text)


# Canonical rank values for Position — lower = higher authority
# 1=executive, 2=vp, 3=director, 4=manager, 5=ic, 99=unknown
POSITION_RANK_EXECUTIVE = 1
POSITION_RANK_VP        = 2
POSITION_RANK_DIRECTOR  = 3
POSITION_RANK_MANAGER   = 4
POSITION_RANK_IC        = 5

POSITION_RANK_LABELS: dict[int, str] = {
    POSITION_RANK_EXECUTIVE: "executive",
    POSITION_RANK_VP:        "vp",
    POSITION_RANK_DIRECTOR:  "director",
    POSITION_RANK_MANAGER:   "manager",
    POSITION_RANK_IC:        "ic",
}


class Position(Base, TenantScoped):
    __tablename__ = "positions"
    id:              Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:     Mapped[Optional[str]]   = mapped_column(String(100))
    name:            Mapped[str]             = mapped_column(String(120))
    level:           Mapped[Optional[str]]   = mapped_column(String(50))
    # rank: 1=executive, 2=vp, 3=director, 4=manager, 5=ic, 99=unknown
    # Lower number = higher in hierarchy. Plans cascade downward.
    rank:            Mapped[int]             = mapped_column(SmallInteger, nullable=False, server_default="99")
    rank_label:      Mapped[Optional[str]]   = mapped_column(String(30), nullable=True)
    source_system:   Mapped[Optional[str]]   = mapped_column(String(50), default="uploaded")
    created_at:      Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_positions_external_id_company"),
    )


class UserProfile(Base, TenantScoped):
    __tablename__ = "users"
    id:              Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:     Mapped[Optional[str]]    = mapped_column(String(100))
    position_id:     Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("positions.id"))
    team_id:         Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("teams.id"))
    name:            Mapped[str]              = mapped_column(String(120))
    email:           Mapped[str]              = mapped_column(String(150))
    region:          Mapped[Optional[str]]    = mapped_column(String(80))
    hire_date:       Mapped[Optional[date]]   = mapped_column(Date)
    source_system:   Mapped[Optional[str]]    = mapped_column(String(50), default="uploaded")
    mapping_basis:   Mapped[Optional[str]]    = mapped_column(String(80))
    evidence_score:  Mapped[Optional[float]]  = mapped_column(Numeric(5, 4))
    created_at:      Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)

    position:        Mapped[Optional[Position]] = relationship()
    team:            Mapped[Optional[Team]]   = relationship()

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id, email). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_users_external_id_company"),
        UniqueConstraint("email", "company_id", name="uq_users_email_company"),
    )


class Manager(Base, TenantScoped):
    __tablename__ = "managers"
    id:                  Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:             Mapped[uuid.UUID]        = mapped_column(ForeignKey("users.id"), unique=True)
    manager_user_id:     Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    source_system:       Mapped[Optional[str]]    = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)


class Plan(Base, TenantScoped):
    __tablename__ = "plans"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]  = mapped_column(String(100))
    name:                Mapped[str]            = mapped_column(String(140))
    description:         Mapped[Optional[str]]  = mapped_column(Text)
    # scope: global | department | team | individual
    # global/department plans owned by executives/directors cascade down the org chart.
    # individual plans are assigned directly via PlanAssignment.
    scope:               Mapped[str]            = mapped_column(String(20), nullable=False, server_default="individual")
    owner_user_id:       Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)

    owner:               Mapped[Optional["UserProfile"]] = relationship(foreign_keys=[owner_user_id])

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_plans_external_id_company"),
    )


class Rule(Base, TenantScoped):
    __tablename__ = "rules"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("plans.id"))
    name:                Mapped[str]            = mapped_column(String(140))
    metric_name:         Mapped[Optional[str]]  = mapped_column(String(80))
    threshold_min:       Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    threshold_max:       Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    rate:                Mapped[Optional[float]] = mapped_column(Numeric(8, 6))
    bonus_amount:        Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    plan:                Mapped[Plan]           = relationship()


class PlanCascadeRule(Base, TenantScoped):
    """
    Defines how a plan owned by an executive/manager cascades down the org chart.

    cascade_scope:
      - all_reports   : apply to every user in the subtree beneath owner_user_id
      - direct_reports: apply only to users whose immediate manager is owner_user_id

    min_rank / max_rank: only apply to users whose position.rank is in [min_rank, max_rank].
      E.g. min_rank=4, max_rank=5 targets managers and ICs, skipping VPs.

    priority: lower = evaluated first. Global/executive rules should use 1–10;
      manager-level overrides 20–50; individual overrides 99.
    """
    __tablename__ = "plan_cascade_rules"
    id:                  Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id:             Mapped[uuid.UUID]        = mapped_column(ForeignKey("plans.id"))
    owner_user_id:       Mapped[uuid.UUID]        = mapped_column(ForeignKey("users.id"))
    cascade_scope:       Mapped[str]              = mapped_column(String(20), nullable=False, server_default="all_reports")
    min_rank:            Mapped[int]              = mapped_column(SmallInteger, nullable=False, server_default="1")
    max_rank:            Mapped[int]              = mapped_column(SmallInteger, nullable=False, server_default="99")
    priority:            Mapped[int]              = mapped_column(SmallInteger, nullable=False, server_default="50")
    effective_start_date: Mapped[Optional[date]]  = mapped_column(Date, nullable=True)
    effective_end_date:   Mapped[Optional[date]]  = mapped_column(Date, nullable=True)
    source_system:       Mapped[Optional[str]]    = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)

    plan:                Mapped[Plan]             = relationship()
    owner:               Mapped["UserProfile"]    = relationship(foreign_keys=[owner_user_id])

    __table_args__ = (
        UniqueConstraint("plan_id", "owner_user_id", "cascade_scope", "effective_start_date",
                         name="uq_plan_cascade_rule"),
    )


class PlanAssignment(Base, TenantScoped):
    __tablename__ = "plan_assignments"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("users.id"))
    plan_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("plans.id"))
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "plan_id", "effective_start_date", name="uq_plan_assignment_window"),
    )


class Territory(Base, TenantScoped):
    __tablename__ = "territories"
    id:                  Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]   = mapped_column(String(100))
    territory_code:      Mapped[Optional[str]]   = mapped_column(String(100))
    name:                Mapped[str]             = mapped_column(String(140))
    parent_territory_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("territories.id"))
    region:              Mapped[Optional[str]]   = mapped_column(String(80))
    segment:             Mapped[Optional[str]]   = mapped_column(String(80))
    source_system:       Mapped[Optional[str]]   = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id, territory_code). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_territories_external_id_company"),
        UniqueConstraint("territory_code", "company_id", name="uq_territories_territory_code_company"),
    )


class UserTerritoryAssignment(Base, TenantScoped):
    __tablename__ = "user_territory_assignments"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("users.id"))
    territory_id:        Mapped[uuid.UUID]      = mapped_column(ForeignKey("territories.id"))
    is_primary:          Mapped[bool]           = mapped_column(Boolean, default=False)
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)


class TerritoryHistory(Base, TenantScoped):
    __tablename__ = "territory_history"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    territory_id:        Mapped[uuid.UUID]      = mapped_column(ForeignKey("territories.id"))
    changed_by_user_id:  Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    change_type:         Mapped[Optional[str]]  = mapped_column(String(80))
    notes:               Mapped[Optional[str]]  = mapped_column(Text)
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class Brand(Base, TenantScoped):
    __tablename__ = "brands"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]  = mapped_column(String(100))
    name:                Mapped[str]            = mapped_column(String(120))
    parent_brand_id:     Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("brands.id"))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id, name). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_brands_external_id_company"),
        UniqueConstraint("name", "company_id", name="uq_brands_name_company"),
    )


class BrandUser(Base, TenantScoped):
    __tablename__ = "brand_users"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id:            Mapped[uuid.UUID]      = mapped_column(ForeignKey("brands.id"))
    user_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("users.id"))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class BrandTerritory(Base, TenantScoped):
    __tablename__ = "brand_territories"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id:            Mapped[uuid.UUID]      = mapped_column(ForeignKey("brands.id"))
    territory_id:        Mapped[uuid.UUID]      = mapped_column(ForeignKey("territories.id"))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class Product(Base, TenantScoped):
    __tablename__ = "products"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]  = mapped_column(String(100))
    product_sku:         Mapped[Optional[str]]  = mapped_column(String(100))
    name:                Mapped[str]            = mapped_column(String(140))
    category:            Mapped[Optional[str]]  = mapped_column(String(80))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id, product_sku). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_products_external_id_company"),
        UniqueConstraint("product_sku", "company_id", name="uq_products_product_sku_company"),
    )


class RepProductAssignment(Base, TenantScoped):
    """Links a sales rep to the products they are authorized/specialised to sell."""
    __tablename__ = "rep_product_assignments"
    id:                   Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id:               Mapped[uuid.UUID]        = mapped_column(ForeignKey("reps.id"))
    product_id:           Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("products.id"))
    is_primary:           Mapped[bool]             = mapped_column(Boolean, default=False)
    specialization:       Mapped[Optional[str]]    = mapped_column(String(80))   # primary_seller | expansion | overlay
    effective_start_date: Mapped[Optional[date]]   = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]]   = mapped_column(Date)
    source_system:        Mapped[Optional[str]]    = mapped_column(String(50), default="uploaded")
    created_at:           Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rep_id", "product_id", name="uq_rep_product"),
    )


class BrandProduct(Base, TenantScoped):
    __tablename__ = "brand_products"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id:            Mapped[uuid.UUID]      = mapped_column(ForeignKey("brands.id"))
    product_id:          Mapped[uuid.UUID]      = mapped_column(ForeignKey("products.id"))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class AccountBrandMap(Base, TenantScoped):
    __tablename__ = "account_brand_maps"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id:          Mapped[uuid.UUID]      = mapped_column(ForeignKey("accounts.id"))
    brand_id:            Mapped[uuid.UUID]      = mapped_column(ForeignKey("brands.id"))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class SalesUnit(Base, TenantScoped):
    __tablename__ = "sales_units"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]  = mapped_column(String(100))
    opportunity_id:      Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("deals.id"))
    account_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"))
    owner_user_id:       Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    booked_date:         Mapped[Optional[date]] = mapped_column(Date)
    amount:              Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    currency:            Mapped[Optional[str]]  = mapped_column(String(10), default="USD")
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_sales_units_external_id_company"),
    )


class SalesUnitLineItem(Base, TenantScoped):
    __tablename__ = "sales_unit_line_items"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sales_unit_id:       Mapped[uuid.UUID]      = mapped_column(ForeignKey("sales_units.id"))
    product_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("products.id"))
    quantity:            Mapped[Optional[int]]  = mapped_column(Integer)
    unit_price:          Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    net_amount:          Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class SalesCredit(Base, TenantScoped):
    __tablename__ = "sales_credits"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sales_unit_id:       Mapped[uuid.UUID]      = mapped_column(ForeignKey("sales_units.id"))
    user_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("users.id"))
    credit_type:         Mapped[Optional[str]]  = mapped_column(String(50))
    credit_percent:      Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    credit_amount:       Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)


class PayoutRecord(Base, TenantScoped):
    __tablename__ = "payouts"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("users.id"))
    plan_id:             Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("plans.id"))
    period:              Mapped[str]            = mapped_column(String(20))
    payout_amount:       Mapped[float]          = mapped_column(Numeric(14, 2), default=0)
    commission_rate:     Mapped[Optional[float]] = mapped_column(Numeric(8, 6))
    fallback_used:       Mapped[bool]           = mapped_column(Boolean, default=False)
    confidence:          Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "period", "plan_id", name="uq_payout_period_plan"),
    )


class Lead(Base, TenantScoped):
    __tablename__ = "leads"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]  = mapped_column(String(100))
    account_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"))
    owner_user_id:       Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    source:              Mapped[Optional[str]]  = mapped_column(String(80))
    status:              Mapped[Optional[str]]  = mapped_column(String(50))
    score:               Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_leads_external_id_company"),
    )


class Opportunity(Base, TenantScoped):
    __tablename__ = "opportunities"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id:         Mapped[Optional[str]]  = mapped_column(String(100))
    account_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"))
    owner_user_id:       Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    name:                Mapped[str]            = mapped_column(String(200))
    stage:               Mapped[Optional[str]]  = mapped_column(String(50))
    amount:              Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    close_date:          Mapped[Optional[date]] = mapped_column(Date)
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Unique per company, not globally: two tenants legitimately use the
    # same natural keys (external_id). A global constraint here made
    # a second company's load collide on the first company's rows.
    __table_args__ = (
        UniqueConstraint("external_id", "company_id", name="uq_opportunities_external_id_company"),
    )


class MonthlyFinance(Base, TenantScoped):
    __tablename__ = "monthly_finance"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"))
    period:              Mapped[str]            = mapped_column(String(20))
    recognized_revenue:  Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    deferred_revenue:    Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    gross_margin:        Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("account_id", "period", name="uq_monthly_finance_account_period"),
    )


class AccountOwnership(Base, TenantScoped):
    __tablename__ = "account_ownership"
    id:                  Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id:          Mapped[uuid.UUID]      = mapped_column(ForeignKey("accounts.id"))
    user_id:             Mapped[uuid.UUID]      = mapped_column(ForeignKey("users.id"))
    role:                Mapped[Optional[str]]  = mapped_column(String(50))
    source_system:       Mapped[Optional[str]]  = mapped_column(String(50), default="uploaded")
    mapping_basis:       Mapped[Optional[str]]  = mapped_column(String(80))
    evidence_score:      Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    effective_start_date: Mapped[Optional[date]] = mapped_column(Date)
    effective_end_date:   Mapped[Optional[date]] = mapped_column(Date)


class ArrWaterfallEntry(Base, TenantScoped):
    """Monthly ARR waterfall per rep — seeded from arr_waterfall.csv."""

    __tablename__ = "arr_waterfall"
    id:               Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id:           Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"), index=True)
    period:           Mapped[str]               = mapped_column(String(20), index=True)
    mrr_new:          Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    mrr_expansion:    Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    mrr_contraction:  Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    mrr_churn:        Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    mrr_renewal:      Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    mrr_net:          Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    arr_start:        Mapped[Optional[float]]   = mapped_column(Numeric(16, 2), default=0)
    arr_end:          Mapped[Optional[float]]   = mapped_column(Numeric(16, 2), default=0)
    created_at:       Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)


class Booking(Base, TenantScoped):
    """Closed-won booking records — seeded from bookings.csv."""

    __tablename__ = "bookings"
    id:                    Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id:               Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("deals.id"), index=True)
    rep_id:                Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"), index=True)
    account_id:            Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"))
    booking_date:          Mapped[Optional[date]]    = mapped_column(Date)
    amount:                Mapped[Optional[float]]   = mapped_column(Numeric(14, 2))
    arr:                   Mapped[Optional[float]]   = mapped_column(Numeric(14, 2))
    mrr:                   Mapped[Optional[float]]   = mapped_column(Numeric(14, 2))
    product_sku:           Mapped[Optional[str]]     = mapped_column(String(100))
    contract_term_months:  Mapped[Optional[int]]     = mapped_column(Integer)
    revenue_type:          Mapped[Optional[str]]     = mapped_column(String(50))
    recognition_start_date: Mapped[Optional[date]]   = mapped_column(Date)
    created_at:            Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)


class ChurnEvent(Base, TenantScoped):
    """Customer churn and contraction events — seeded from churn_events.csv."""

    __tablename__ = "churn_events"
    id:           Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id:   Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("accounts.id"), index=True)
    period:       Mapped[str]               = mapped_column(String(20), index=True)
    event_type:   Mapped[Optional[str]]     = mapped_column(String(50))   # partial_contraction, full_churn
    arr_change:   Mapped[Optional[float]]   = mapped_column(Numeric(14, 2))  # negative = loss
    reason:       Mapped[Optional[str]]     = mapped_column(String(100))
    detected_at:  Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)
    created_at:   Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)


Index("idx_users_external_id", UserProfile.external_id)
Index("idx_territories_code", Territory.territory_code)
Index("idx_products_sku", Product.product_sku)
Index("idx_opportunities_owner", Opportunity.owner_user_id)
Index("idx_payouts_user_period", PayoutRecord.user_id, PayoutRecord.period)
Index("idx_arr_waterfall_period", ArrWaterfallEntry.period)
Index("idx_bookings_rep_date", Booking.rep_id, Booking.booking_date)
Index("idx_churn_events_period", ChurnEvent.period)


class AttainmentSnapshot(Base, TenantScoped):
    """Monthly and quarterly attainment snapshots per rep — seeded from attainment_snapshots.csv."""

    __tablename__ = "attainment_snapshots"
    id:              Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id:          Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"), index=True)
    period:          Mapped[str]               = mapped_column(String(20), index=True)
    grain:           Mapped[Optional[str]]     = mapped_column(String(20))   # monthly | quarterly
    revenue:         Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    quota:           Mapped[Optional[float]]   = mapped_column(Numeric(14, 2), default=0)
    attainment_pct:  Mapped[Optional[float]]   = mapped_column(Numeric(10, 4), default=0)
    snapshot_date:   Mapped[Optional[date]]    = mapped_column(Date)
    created_at:      Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rep_id", "period", "grain", name="uq_attainment_snapshot"),
    )


class RepRamp(Base, TenantScoped):
    """Rep ramp tracking over monthly periods — seeded from rep_ramp.csv."""

    __tablename__ = "rep_ramp"
    id:                Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id:            Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("reps.id"), index=True)
    period:            Mapped[str]               = mapped_column(String(20), index=True)
    months_since_hire: Mapped[Optional[int]]     = mapped_column(Integer)
    ramp_factor:       Mapped[Optional[float]]   = mapped_column(Numeric(5, 4))
    quota_at_ramp:     Mapped[Optional[float]]   = mapped_column(Numeric(14, 2))
    full_quota:        Mapped[Optional[float]]   = mapped_column(Numeric(14, 2))
    is_ramping:        Mapped[Optional[bool]]    = mapped_column(Boolean)
    created_at:        Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rep_id", "period", name="uq_rep_ramp_period"),
    )


Index("idx_attainment_snapshots_period", AttainmentSnapshot.period)
Index("idx_rep_ramp_period", RepRamp.period)


class PayoutConfiguration(Base):  # already declares its own, stricter company_id
    """Persistent payout config by company/tenant with versioning."""
    __tablename__ = "payout_configs"
    id:               Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id:       Mapped[str]              = mapped_column(String(100), index=True)
    version:          Mapped[int]              = mapped_column(Integer, default=1)
    config_json:      Mapped[dict]             = mapped_column(JSONB, nullable=False)
    effective_date:   Mapped[Optional[date]]   = mapped_column(Date)
    is_active:        Mapped[bool]             = mapped_column(Boolean, default=True)
    created_at:       Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:       Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("company_id", "version", name="uq_payout_config_company_version"),
    )


class JobStatus(Base):
    """Background job status tracking."""
    __tablename__ = "job_status"
    id:            Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type:      Mapped[str]              = mapped_column(String(100), index=True)
    status:        Mapped[str]              = mapped_column(String(20), default="queued")  # queued|running|success|failed
    progress:      Mapped[Optional[int]]    = mapped_column(Integer)
    metadata_json: Mapped[Optional[dict]]   = mapped_column(JSONB)
    error_message: Mapped[Optional[str]]    = mapped_column(Text)
    started_at:    Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at:   Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at:    Mapped[datetime]         = mapped_column(DateTime, default=datetime.utcnow)


# Tenant scoping is registered here, at the bottom of the module that defines
# TenantScoped, so it is active for every session in the process — API requests,
# scripts and tests alike — rather than depending on an import somewhere else.
from backend.tenant_guard import install as _install_tenant_guard  # noqa: E402

_install_tenant_guard(TenantScoped)
