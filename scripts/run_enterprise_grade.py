from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.grading.enterprise_grader import EnterpriseGrader


def main() -> None:
    repo_root = PROJECT_ROOT
    grader = EnterpriseGrader(str(repo_root))
    result = grader.run()

    print(f"Enterprise Readiness Score: {result['overall_score']} / 100 - Grade {result['grade']}")
    print("\nCategory Scorecard:")
    for category in result["categories"]:
        print(f"- {category['name']}: {category['score']} / {category['max_score']}")

    print("\nCritical Gaps:")
    if result["critical_gaps"]:
        for gap in result["critical_gaps"]:
            print(f"- {gap}")
    else:
        print("- None")

    print("\nRecommendations:")
    for idx, rec in enumerate(result["recommendations"], start=1):
        print(f"{idx}. {rec}")


if __name__ == "__main__":
    main()
