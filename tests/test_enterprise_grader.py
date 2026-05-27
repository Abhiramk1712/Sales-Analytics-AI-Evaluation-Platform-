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
