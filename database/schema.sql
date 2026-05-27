-- ============================================================
--  Sales Analytics AI  ·  PostgreSQL Schema
--  Master's Project · Data Science & ML
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Teams ────────────────────────────────────────────────────
CREATE TABLE teams (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100) NOT NULL,
    region      VARCHAR(50),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ── Sales Reps ───────────────────────────────────────────────
CREATE TABLE reps (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id         UUID REFERENCES teams(id) ON DELETE SET NULL,
    name            VARCHAR(100) NOT NULL,
    email           VARCHAR(150) UNIQUE NOT NULL,
    region          VARCHAR(50),
    hire_date       DATE,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ── Quotas (per rep, per period) ─────────────────────────────
CREATE TABLE quotas (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rep_id      UUID REFERENCES reps(id) ON DELETE CASCADE,
    period      VARCHAR(20) NOT NULL,          -- e.g. '2025-Q2'
    amount      NUMERIC(14,2) NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(rep_id, period)
);

-- ── Accounts (companies) ─────────────────────────────────────
CREATE TABLE accounts (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(150) NOT NULL,
    industry    VARCHAR(80),
    employee_count INTEGER,
    annual_revenue  NUMERIC(16,2),
    country     VARCHAR(80) DEFAULT 'United States',
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ── Deals ────────────────────────────────────────────────────
CREATE TABLE deals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID REFERENCES accounts(id) ON DELETE SET NULL,
    rep_id          UUID REFERENCES reps(id) ON DELETE SET NULL,
    name            VARCHAR(200) NOT NULL,
    product         VARCHAR(100),
    stage           VARCHAR(50) NOT NULL
                        CHECK (stage IN ('Prospecting','Qualification','Proposal',
                                         'Negotiation','Closed Won','Closed Lost')),
    amount          NUMERIC(14,2) NOT NULL DEFAULT 0,
    close_probability  SMALLINT CHECK (close_probability BETWEEN 0 AND 100),
    expected_close_date DATE,
    actual_close_date   DATE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ── Activities ───────────────────────────────────────────────
CREATE TABLE activities (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id     UUID REFERENCES deals(id) ON DELETE CASCADE,
    rep_id      UUID REFERENCES reps(id) ON DELETE SET NULL,
    type        VARCHAR(50) CHECK (type IN ('call','email','meeting','demo','follow_up')),
    outcome     VARCHAR(50) CHECK (outcome IN ('positive','neutral','negative','no_response')),
    notes       TEXT,
    activity_date TIMESTAMP DEFAULT NOW()
);

-- ── Revenue (monthly actuals) ────────────────────────────────
CREATE TABLE revenue (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rep_id      UUID REFERENCES reps(id) ON DELETE SET NULL,
    period      VARCHAR(20) NOT NULL,          -- 'YYYY-MM'
    amount      NUMERIC(14,2) NOT NULL DEFAULT 0,
    recorded_at TIMESTAMP DEFAULT NOW()
);

-- ── ML Predictions ───────────────────────────────────────────
CREATE TABLE ml_predictions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name      VARCHAR(100) NOT NULL,     -- 'revenue_forecast', 'deal_score', 'rep_cluster'
    entity_type     VARCHAR(50),               -- 'deal', 'rep', 'team'
    entity_id       UUID,
    prediction      JSONB NOT NULL,            -- flexible: scores, forecasts, cluster labels
    confidence      NUMERIC(5,4),
    model_version   VARCHAR(20),
    predicted_at    TIMESTAMP DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────────
CREATE INDEX idx_deals_rep     ON deals(rep_id);
CREATE INDEX idx_deals_stage   ON deals(stage);
CREATE INDEX idx_deals_close   ON deals(expected_close_date);
CREATE INDEX idx_revenue_rep   ON revenue(rep_id, period);
CREATE INDEX idx_activities_deal ON activities(deal_id);
CREATE INDEX idx_ml_entity     ON ml_predictions(entity_type, entity_id);

-- ── Updated-at trigger ───────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_deals_updated_at
BEFORE UPDATE ON deals
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
--  Phase 2b: Manifest-Native Enterprise Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS positions (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    name                    VARCHAR(120) NOT NULL,
    level                   VARCHAR(50),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    position_id             UUID REFERENCES positions(id) ON DELETE SET NULL,
    team_id                 UUID REFERENCES teams(id) ON DELETE SET NULL,
    name                    VARCHAR(120) NOT NULL,
    email                   VARCHAR(150) UNIQUE NOT NULL,
    region                  VARCHAR(80),
    hire_date               DATE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE TABLE IF NOT EXISTS managers (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    manager_user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plans (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    name                    VARCHAR(140) NOT NULL,
    description             TEXT,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE TABLE IF NOT EXISTS rules (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plan_id                 UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    name                    VARCHAR(140) NOT NULL,
    metric_name             VARCHAR(80),
    threshold_min           NUMERIC(14,2),
    threshold_max           NUMERIC(14,2),
    rate                    NUMERIC(8,6),
    bonus_amount            NUMERIC(14,2),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plan_assignments (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id                 UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    effective_start_date    DATE,
    effective_end_date      DATE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, plan_id, effective_start_date)
);

CREATE TABLE IF NOT EXISTS territories (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    territory_code          VARCHAR(100) UNIQUE,
    name                    VARCHAR(140) NOT NULL,
    parent_territory_id     UUID REFERENCES territories(id) ON DELETE SET NULL,
    region                  VARCHAR(80),
    segment                 VARCHAR(80),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE TABLE IF NOT EXISTS user_territory_assignments (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    territory_id            UUID NOT NULL REFERENCES territories(id) ON DELETE CASCADE,
    is_primary              BOOLEAN DEFAULT FALSE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE TABLE IF NOT EXISTS territory_history (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    territory_id            UUID NOT NULL REFERENCES territories(id) ON DELETE CASCADE,
    changed_by_user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    change_type             VARCHAR(80),
    notes                   TEXT,
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS brands (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    name                    VARCHAR(120) UNIQUE NOT NULL,
    parent_brand_id         UUID REFERENCES brands(id) ON DELETE SET NULL,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE TABLE IF NOT EXISTS brand_users (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    brand_id                UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS brand_territories (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    brand_id                UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    territory_id            UUID NOT NULL REFERENCES territories(id) ON DELETE CASCADE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    product_sku             VARCHAR(100) UNIQUE,
    name                    VARCHAR(140) NOT NULL,
    category                VARCHAR(80),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS brand_products (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    brand_id                UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    product_id              UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_brand_maps (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id              UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    brand_id                UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_units (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    opportunity_id          UUID REFERENCES deals(id) ON DELETE SET NULL,
    account_id              UUID REFERENCES accounts(id) ON DELETE SET NULL,
    owner_user_id           UUID REFERENCES users(id) ON DELETE SET NULL,
    booked_date             DATE,
    amount                  NUMERIC(14,2),
    currency                VARCHAR(10) DEFAULT 'USD',
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_unit_line_items (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sales_unit_id           UUID NOT NULL REFERENCES sales_units(id) ON DELETE CASCADE,
    product_id              UUID REFERENCES products(id) ON DELETE SET NULL,
    quantity                INTEGER,
    unit_price              NUMERIC(14,2),
    net_amount              NUMERIC(14,2),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_credits (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sales_unit_id           UUID NOT NULL REFERENCES sales_units(id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credit_type             VARCHAR(50),
    credit_percent          NUMERIC(8,4),
    credit_amount           NUMERIC(14,2),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payouts (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id                 UUID REFERENCES plans(id) ON DELETE SET NULL,
    period                  VARCHAR(20) NOT NULL,
    payout_amount           NUMERIC(14,2) NOT NULL DEFAULT 0,
    commission_rate         NUMERIC(8,6),
    fallback_used           BOOLEAN DEFAULT FALSE,
    confidence              NUMERIC(5,4),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, period, plan_id)
);

CREATE TABLE IF NOT EXISTS leads (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    account_id              UUID REFERENCES accounts(id) ON DELETE SET NULL,
    owner_user_id           UUID REFERENCES users(id) ON DELETE SET NULL,
    source                  VARCHAR(80),
    status                  VARCHAR(50),
    score                   NUMERIC(8,2),
    created_at              TIMESTAMP DEFAULT NOW(),
    source_system           VARCHAR(50) DEFAULT 'uploaded'
);

CREATE TABLE IF NOT EXISTS opportunities (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id             VARCHAR(100) UNIQUE,
    account_id              UUID REFERENCES accounts(id) ON DELETE SET NULL,
    owner_user_id           UUID REFERENCES users(id) ON DELETE SET NULL,
    name                    VARCHAR(200) NOT NULL,
    stage                   VARCHAR(50),
    amount                  NUMERIC(14,2),
    close_date              DATE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    updated_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monthly_finance (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id              UUID REFERENCES accounts(id) ON DELETE SET NULL,
    period                  VARCHAR(20) NOT NULL,
    recognized_revenue      NUMERIC(14,2),
    deferred_revenue        NUMERIC(14,2),
    gross_margin            NUMERIC(8,4),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE(account_id, period)
);

CREATE TABLE IF NOT EXISTS account_ownership (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id              UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role                    VARCHAR(50),
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    mapping_basis           VARCHAR(80),
    evidence_score          NUMERIC(5,4),
    created_at              TIMESTAMP DEFAULT NOW(),
    effective_start_date    DATE,
    effective_end_date      DATE
);

CREATE INDEX IF NOT EXISTS idx_users_external_id ON users(external_id);
CREATE INDEX IF NOT EXISTS idx_territories_code ON territories(territory_code);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(product_sku);
CREATE INDEX IF NOT EXISTS idx_opportunities_owner ON opportunities(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_payouts_user_period ON payouts(user_id, period);

-- ============================================================
--  Phase 2: ORM-aligned tables missing from original schema
-- ============================================================

-- ── Revenue: RevOps extended columns ────────────────────────
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS account_id              UUID REFERENCES accounts(id) ON DELETE SET NULL;
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS deal_id                 UUID REFERENCES deals(id)    ON DELETE SET NULL;
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS revenue_type            VARCHAR(40);
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS contract_term_months    INTEGER;
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS recognition_start_date  DATE;
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS product_sku             VARCHAR(80);
ALTER TABLE revenue ADD COLUMN IF NOT EXISTS is_recurring            BOOLEAN;

-- ── ML model run registry ────────────────────────────────────
CREATE TABLE IF NOT EXISTS model_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name      VARCHAR(100) NOT NULL,
    model_version   VARCHAR(40),
    trained_at      TIMESTAMP DEFAULT NOW(),
    training_rows   INTEGER,
    feature_names   JSONB,
    target_name     VARCHAR(100),
    metrics         JSONB,
    limitations     JSONB,
    artifact_path   VARCHAR(255),
    data_hash       VARCHAR(64),
    notes           TEXT
);

-- ── Rep ↔ Product assignments ────────────────────────────────
CREATE TABLE IF NOT EXISTS rep_product_assignments (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rep_id                  UUID NOT NULL REFERENCES reps(id) ON DELETE CASCADE,
    product_id              UUID REFERENCES products(id) ON DELETE SET NULL,
    is_primary              BOOLEAN DEFAULT FALSE,
    specialization          VARCHAR(80),
    effective_start_date    DATE,
    effective_end_date      DATE,
    source_system           VARCHAR(50) DEFAULT 'uploaded',
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE(rep_id, product_id)
);

-- ── ARR waterfall (monthly per rep) ──────────────────────────
CREATE TABLE IF NOT EXISTS arr_waterfall (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rep_id              UUID REFERENCES reps(id) ON DELETE SET NULL,
    period              VARCHAR(20) NOT NULL,
    mrr_new             NUMERIC(14,2) DEFAULT 0,
    mrr_expansion       NUMERIC(14,2) DEFAULT 0,
    mrr_contraction     NUMERIC(14,2) DEFAULT 0,
    mrr_churn           NUMERIC(14,2) DEFAULT 0,
    mrr_renewal         NUMERIC(14,2) DEFAULT 0,
    mrr_net             NUMERIC(14,2) DEFAULT 0,
    arr_start           NUMERIC(16,2) DEFAULT 0,
    arr_end             NUMERIC(16,2) DEFAULT 0,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ── Bookings (closed-won) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS bookings (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id                 UUID REFERENCES deals(id) ON DELETE SET NULL,
    rep_id                  UUID REFERENCES reps(id) ON DELETE SET NULL,
    account_id              UUID REFERENCES accounts(id) ON DELETE SET NULL,
    booking_date            DATE,
    amount                  NUMERIC(14,2),
    arr                     NUMERIC(14,2),
    mrr                     NUMERIC(14,2),
    product_sku             VARCHAR(100),
    contract_term_months    INTEGER,
    revenue_type            VARCHAR(50),
    recognition_start_date  DATE,
    created_at              TIMESTAMP DEFAULT NOW()
);

-- ── Churn events ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS churn_events (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id  UUID REFERENCES accounts(id) ON DELETE SET NULL,
    period      VARCHAR(20) NOT NULL,
    event_type  VARCHAR(50),
    arr_change  NUMERIC(14,2),
    reason      VARCHAR(100),
    detected_at TIMESTAMP DEFAULT NOW(),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ── Phase 2 indexes ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_arr_waterfall_period   ON arr_waterfall(period);
CREATE INDEX IF NOT EXISTS idx_arr_waterfall_rep      ON arr_waterfall(rep_id, period);
CREATE INDEX IF NOT EXISTS idx_bookings_rep_date      ON bookings(rep_id, booking_date);
CREATE INDEX IF NOT EXISTS idx_churn_events_period    ON churn_events(period);
CREATE INDEX IF NOT EXISTS idx_rep_products_rep       ON rep_product_assignments(rep_id);
CREATE INDEX IF NOT EXISTS idx_revenue_account        ON revenue(account_id);
CREATE INDEX IF NOT EXISTS idx_revenue_type           ON revenue(revenue_type);

-- ── Phase 3: Hierarchical plan rank & cascade ────────────────
-- Add rank + rank_label to positions
-- rank: 1=executive, 2=vp, 3=director, 4=manager, 5=ic, 99=unknown
ALTER TABLE positions ADD COLUMN IF NOT EXISTS rank       SMALLINT NOT NULL DEFAULT 99;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS rank_label VARCHAR(30);

-- Add scope + owner to plans
-- scope: global | department | team | individual
ALTER TABLE plans ADD COLUMN IF NOT EXISTS scope         VARCHAR(20) NOT NULL DEFAULT 'individual';
ALTER TABLE plans ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- Plan cascade rules: how executive/manager plans cascade down the org chart
CREATE TABLE IF NOT EXISTS plan_cascade_rules (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plan_id              UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    owner_user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- all_reports | direct_reports
    cascade_scope        VARCHAR(20) NOT NULL DEFAULT 'all_reports',
    -- apply only to users whose position.rank is within [min_rank, max_rank]
    min_rank             SMALLINT NOT NULL DEFAULT 1,
    max_rank             SMALLINT NOT NULL DEFAULT 99,
    -- lower = evaluated first; global rules = 1-10, manager overrides = 20-50, individual = 99
    priority             SMALLINT NOT NULL DEFAULT 50,
    effective_start_date DATE,
    effective_end_date   DATE,
    source_system        VARCHAR(50) DEFAULT 'uploaded',
    created_at           TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_plan_cascade_rule
        UNIQUE (plan_id, owner_user_id, cascade_scope, effective_start_date)
);

-- Phase 3 indexes
CREATE INDEX IF NOT EXISTS idx_positions_rank         ON positions(rank);
CREATE INDEX IF NOT EXISTS idx_plans_scope            ON plans(scope);
CREATE INDEX IF NOT EXISTS idx_plans_owner            ON plans(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_pcr_owner              ON plan_cascade_rules(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_pcr_plan               ON plan_cascade_rules(plan_id);
CREATE INDEX IF NOT EXISTS idx_pcr_priority           ON plan_cascade_rules(priority, effective_start_date);

-- ── Phase B1: AttainmentSnapshot + RepRamp ───────────────────

CREATE TABLE IF NOT EXISTS attainment_snapshots (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rep_id           UUID REFERENCES reps(id) ON DELETE CASCADE,
    period           VARCHAR(20) NOT NULL,
    grain            VARCHAR(20),
    revenue          NUMERIC(14,2) DEFAULT 0,
    quota            NUMERIC(14,2) DEFAULT 0,
    attainment_pct   NUMERIC(10,4) DEFAULT 0,
    snapshot_date    DATE,
    created_at       TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_attainment_snapshot UNIQUE (rep_id, period, grain)
);

CREATE TABLE IF NOT EXISTS rep_ramp (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rep_id            UUID REFERENCES reps(id) ON DELETE CASCADE,
    period            VARCHAR(20) NOT NULL,
    months_since_hire INTEGER,
    ramp_factor       NUMERIC(5,4),
    quota_at_ramp     NUMERIC(14,2),
    full_quota        NUMERIC(14,2),
    is_ramping        BOOLEAN,
    created_at        TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_rep_ramp_period UNIQUE (rep_id, period)
);

CREATE INDEX IF NOT EXISTS idx_attainment_snapshots_rep    ON attainment_snapshots(rep_id);
CREATE INDEX IF NOT EXISTS idx_attainment_snapshots_period ON attainment_snapshots(period);
CREATE INDEX IF NOT EXISTS idx_rep_ramp_rep                ON rep_ramp(rep_id);
CREATE INDEX IF NOT EXISTS idx_rep_ramp_period             ON rep_ramp(period);
