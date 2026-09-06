from pathlib import Path

from backend.grading.enterprise_grader import EnterpriseGrader


def test_enterprise_grader_returns_scorecard():
    repo_root = Path(__file__).resolve().parents[1]
    grader = EnterpriseGrader(str(repo_root))
    result = grader.run()

    assert 0 <= result["overall_score"] <= 100
    assert result["grade"] in {"A", "B", "C", "D", "F"}
    assert isinstance(result["categories"], list)
    assert "generated_at" in result


def test_forecast_short_history_fallback_check_actually_passes():
    """The "ML workflow" category's short-history-fallback check used to
    build 12 months of sample data (run_revenue_forecast()'s "trend" bucket
    is 6-23 months) and then assert forecast_mode == "baseline" (the <6
    month bucket) -- a combination the fallback path can never produce, so
    this check failed unconditionally and was listed as a permanent
    "critical gap" / dragged the ML workflow score to 10/12, even though
    the short-history fallback it names works exactly as designed.

    This isn't a smoke test on the whole grader (test_enterprise_grader_
    returns_scorecard above already does that) -- it targets the one check
    that was actually broken, on this actual repo checkout, at the exact
    label the endpoint / UI shows it under."""
    repo_root = Path(__file__).resolve().parents[1]
    grader = EnterpriseGrader(str(repo_root))
    result = grader.run()

    ml_category = next(c for c in result["categories"] if c["name"] == "ML workflow")
    check = next(c for c in ml_category["checks"] if c["criterion"] == "forecast short-history fallback works")
    assert check["passed"] is True
    assert "forecast short-history fallback works" not in result["critical_gaps"]


def test_all_functional_checks_pass_on_this_repo():
    """The "FUNCTIONAL PASS" metric on the Enterprise Grade tab showed
    85.7% (30/35) on this exact, working repo checkout because 5 checks
    were each broken in the check-dispatch plumbing, not in the thing they
    were meant to verify:

    - revenue_saas_fields: module_or_path was "backend.models.Revenue" --
      not a real importable module (Revenue is a class inside backend.models,
      not a submodule) -- so importlib.import_module() always raised.
    - period_quarter_parsing: asserted r.start_date.month, but
      PeriodRange.start_date is a "YYYY-MM-DD" string with no .month
      attribute -- always raised.
    - test_coverage_200plus, gitignore_env, ml_saved_artifacts: all three
      have correct check-specific logic later in _run_functional_check(),
      but the *generic* path-based dispatch above it intercepted them
      first: "tests/" and "backend/ml/saved/" both end in "/" and hit the
      "directory needs >= 5 markdown files" branch meant for
      knowledge_base_docs (0 .md files in either -- always False); the
      bare ".gitignore" has no "/" at all, so it fell through to
      importlib.import_module(".gitignore"), which always raised. Once
      test_coverage_200plus's own logic could run at all, it had its own,
      second bug: -q passed twice collapses pytest --collect-only's output
      from one line per test to one summary line per *file*, permanently
      under the 200-line threshold regardless of the real test count.

    Every one of the 5 was a dispatch/plumbing bug, not a real gap in this
    codebase -- confirmed by fixing the dispatch and observing all 5 pass
    without touching the thing each one actually verifies."""
    repo_root = Path(__file__).resolve().parents[1]
    grader = EnterpriseGrader(str(repo_root))
    result = grader.run()

    failing = [f for f in result["functional_checks"] if not f["passed"]]
    assert failing == [], f"expected all functional checks to pass, but {failing} failed"
    assert result["functional_pass_rate"] == 100.0
