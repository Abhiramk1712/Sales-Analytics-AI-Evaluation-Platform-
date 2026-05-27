"""
backend/metrics/definitions.py
==============================
Metric definitions for the sales analytics platform.

Each metric includes:
- name: Unique identifier
- display_name: Human-readable name
- description: What the metric measures
- formula: How it's calculated
- required_fields: Database columns needed
- grain: Level of aggregation (rep, region, team, company)
- owner: Team responsible for this metric
- caveats: Important limitations or assumptions
"""
from dataclasses import dataclass
from typing import Optional, Set


@dataclass
class MetricDefinition:
    """Definition of a single metric."""
    
    name: str
    display_name: str
    description: str
    formula: str
    required_fields: list[str]
    grain: str  # 'rep', 'region', 'team', 'company'
    owner: str
    caveats: Optional[list[str]] = None
    
    def __post_init__(self):
        """Validate metric definition."""
        if not self.name or not self.formula:
            raise ValueError("Metric name and formula are required")
        if not self.required_fields:
            raise ValueError("At least one required field must be specified")


# Core metrics used throughout the platform
METRICS = {
    "total_revenue": MetricDefinition(
        name="total_revenue",
        display_name="Total Revenue",
        description="Sum of recognized revenue rows in the selected scope and period",
        formula="SUM(revenue.amount)",
        required_fields=["revenue.amount", "revenue.period"],
        grain="company",
        owner="Finance",
        caveats=["Assumes revenue table stores booked/recognized revenue", "Does not infer revenue from open pipeline"],
    ),

    "total_quota": MetricDefinition(
        name="total_quota",
        display_name="Total Quota",
        description="Sum of assigned quota for the selected period and filters",
        formula="SUM(quota.amount)",
        required_fields=["quota.amount", "quota.period"],
        grain="company",
        owner="Sales Operations",
        caveats=["Period alignment is required for fair comparisons"],
    ),
    
    "commissionable_revenue": MetricDefinition(
        name="commissionable_revenue",
        display_name="Commissionable Revenue",
        description="Revenue eligible for sales commissions",
        formula="SUM(revenue.amount) for deals meeting commission criteria",
        required_fields=["revenue.amount", "deal.stage", "deal.actual_close_date"],
        grain="company",
        owner="Sales Operations",
        caveats=["Commission rules may vary by product/region", "Subject to true-ups"],
    ),
    
    "quota_attainment": MetricDefinition(
        name="quota_attainment",
        display_name="Quota Attainment %",
        description="Actual revenue divided by assigned quota",
        formula="total_revenue / quota * 100",
        required_fields=["revenue.amount", "quota.amount"],
        grain="rep",
        owner="Sales Operations",
        caveats=["Assumes quota is assigned for all periods", "May vary by region"],
    ),
    
    "win_rate": MetricDefinition(
        name="win_rate",
        display_name="Win Rate %",
        description="Percentage of closed deals that were won",
        formula="COUNT(deals WHERE stage='Closed Won') / COUNT(deals WHERE stage IN ('Closed Won', 'Closed Lost')) * 100",
        required_fields=["deal.stage", "deal.id"],
        grain="rep",
        owner="Sales Operations",
        caveats=["Only considers closed deals", "Excludes open/stalled opportunities"],
    ),
    
    "pipeline_coverage": MetricDefinition(
        name="pipeline_coverage",
        display_name="Pipeline Coverage Ratio",
        description="Ratio of open pipeline to selected-period quota",
        formula="open_pipeline_value / selected_period_quota",
        required_fields=["deal.amount", "deal.stage", "quota.amount", "quota.period"],
        grain="rep",
        owner="Sales Leadership",
        caveats=["Pipeline coverage depends on quota grain: monthly, quarterly, or annual", "Pipeline is probabilistic and stage quality dependent"],
    ),

    "open_pipeline": MetricDefinition(
        name="open_pipeline",
        display_name="Open Pipeline",
        description="Total value of opportunities that are not yet closed",
        formula="SUM(deal.amount WHERE deal.stage NOT IN ('Closed Won', 'Closed Lost'))",
        required_fields=["deal.amount", "deal.stage"],
        grain="company",
        owner="Sales Leadership",
        caveats=["Pipeline includes probabilistic opportunities", "Stage hygiene affects accuracy"],
    ),
    
    "average_deal_size": MetricDefinition(
        name="average_deal_size",
        display_name="Average Deal Size",
        description="Mean value of closed deals",
        formula="SUM(deal.amount WHERE stage='Closed Won') / COUNT(deals WHERE stage='Closed Won')",
        required_fields=["deal.amount", "deal.stage"],
        grain="rep",
        owner="Sales Leadership",
        caveats=["Outliers may skew average", "Consider median for better picture"],
    ),
    
    "forecasted_revenue": MetricDefinition(
        name="forecasted_revenue",
        display_name="Forecasted Revenue",
        description="Projected revenue for the period using ML model",
        formula="ML ensemble forecast (SARIMAX + Ridge regression)",
        required_fields=["revenue.amount", "revenue.period"],
        grain="company",
        owner="Analytics",
        caveats=["Forecast confidence varies by history length", "Assumes stationarity"],
    ),
    
    "cost_of_sales": MetricDefinition(
        name="cost_of_sales",
        display_name="Cost of Sales",
        description="Burden cost for sales operations (salaries, tools, etc.)",
        formula="SUM(budget allocations for sales)",
        required_fields=["cost_allocation"],
        grain="company",
        owner="Finance",
        caveats=["May not include all overhead", "Allocation methodology subject to change"],
    ),
    
    "rep_risk_score": MetricDefinition(
        name="rep_risk_score",
        display_name="Rep Risk Score",
        description="Composite metric indicating rep performance risk (0-100)",
        formula="Weighted combination of quota attainment, win rate, activity metrics",
        required_fields=["revenue.amount", "quota.amount", "deal.stage", "activity.count"],
        grain="rep",
        owner="Sales Leadership",
        caveats=["Weights are subjective", "Should be used with qualitative assessment"],
    ),
    
    "sales_cycle_length": MetricDefinition(
        name="sales_cycle_length",
        display_name="Sales Cycle Length (days)",
        description="Average days from deal creation to close",
        formula="AVG(deal.actual_close_date - deal.created_at) for closed deals",
        required_fields=["deal.created_at", "deal.actual_close_date", "deal.stage"],
        grain="rep",
        owner="Sales Leadership",
        caveats=["Excludes stalled deals", "May vary significantly by deal type"],
    ),

    # ── RevOps metrics ───────────────────────────────────────────────────────
    "nrr": MetricDefinition(
        name="nrr",
        display_name="Net Revenue Retention (NRR) %",
        description=(
            "Percentage of recurring revenue retained from existing customers plus expansion, "
            "net of contraction and churn. NRR > 100% means the existing base is growing."
        ),
        formula="(MRR_start + expansion - contraction - churn) / MRR_start * 100",
        required_fields=["revenue.amount", "revenue.revenue_type", "revenue.period"],
        grain="company",
        owner="Sales Operations",
        caveats=[
            "Requires revenue_type classification on revenue rows",
            "Best measured over a rolling 12-month window",
            "NRR < 100% signals the base is shrinking even before new logo is counted",
        ],
    ),

    "grr": MetricDefinition(
        name="grr",
        display_name="Gross Revenue Retention (GRR) %",
        description=(
            "Percentage of revenue retained from existing customers before expansion. "
            "GRR caps at 100% and excludes upsell/expansion."
        ),
        formula="(MRR_start - contraction - churn) / MRR_start * 100",
        required_fields=["revenue.amount", "revenue.revenue_type", "revenue.period"],
        grain="company",
        owner="Finance",
        caveats=[
            "Requires revenue_type classification on revenue rows",
            "Healthy GRR benchmark for SaaS: > 85%",
        ],
    ),

    "arr_growth_rate": MetricDefinition(
        name="arr_growth_rate",
        display_name="ARR Growth Rate % (YoY)",
        description="Year-over-year percentage growth of Annual Recurring Revenue.",
        formula="(ARR_current_period - ARR_same_period_last_year) / ARR_same_period_last_year * 100",
        required_fields=["revenue.amount", "revenue.period"],
        grain="company",
        owner="Finance",
        caveats=[
            "Requires at least 13 months of revenue history",
            "ARR is annualised from monthly revenue rows",
        ],
    ),

    "sales_cycle_days": MetricDefinition(
        name="sales_cycle_days",
        display_name="Avg Sales Cycle (days)",
        description="Average calendar days from deal creation to Closed Won.",
        formula="AVG(actual_close_date - created_at) WHERE stage = 'Closed Won'",
        required_fields=["deal.created_at", "deal.actual_close_date", "deal.stage"],
        grain="company",
        owner="Sales Operations",
        caveats=["Only Closed Won deals are counted", "Outliers > 365 days are excluded"],
    ),

    "activity_ratio": MetricDefinition(
        name="activity_ratio",
        display_name="Activity-to-Deal Ratio",
        description="Average number of activities per open deal, indicating pipeline engagement quality.",
        formula="COUNT(activities WHERE deal in open_pipeline) / COUNT(open_deals)",
        required_fields=["activity.deal_id", "deal.stage"],
        grain="rep",
        owner="Sales Leadership",
        caveats=["Higher ratio is not always better; quality matters", "Benchmarks vary by product/segment"],
    ),

    "weighted_pipeline_coverage": MetricDefinition(
        name="weighted_pipeline_coverage",
        display_name="Weighted Pipeline Coverage",
        description=(
            "Pipeline coverage ratio using stage-probability-weighted deal amounts "
            "rather than raw amounts. More conservative than unweighted coverage."
        ),
        formula="SUM(deal.amount * stage_probability / 100) / quota_remaining",
        required_fields=["deal.amount", "deal.stage", "deal.close_probability", "quota.amount"],
        grain="rep",
        owner="Sales Leadership",
        caveats=[
            "Healthy benchmark: 3× weighted coverage for quarterly quota",
            "Raw (unweighted) coverage benchmark: 4×–5× quarterly quota",
        ],
    ),

    "quota_attainment_distribution": MetricDefinition(
        name="quota_attainment_distribution",
        display_name="Quota Attainment Distribution",
        description=(
            "Percentage of reps in each attainment tier: "
            "below_50, 50_to_75, 75_to_100, 100_to_120, above_120."
        ),
        formula="COUNT(reps per attainment_tier) / total_reps * 100",
        required_fields=["revenue.amount", "quota.amount", "quota.period", "revenue.period"],
        grain="company",
        owner="Sales Operations",
        caveats=[
            "Healthy distribution: > 60% of reps at 100%+",
            "If > 30% below 50% attainment, quota-setting process should be reviewed",
        ],
    ),
}


def get_all_metric_definitions() -> dict[str, MetricDefinition]:
    """Get all metric definitions."""
    return METRICS.copy()


def get_metric_definition(name: str) -> Optional[MetricDefinition]:
    """Get a specific metric definition by name."""
    return METRICS.get(name)


def metric_exists(name: str) -> bool:
    """Check if a metric is defined."""
    return name in METRICS


def list_metrics_by_grain(grain: str) -> list[MetricDefinition]:
    """Get all metrics at a specific grain level."""
    return [m for m in METRICS.values() if m.grain == grain]
