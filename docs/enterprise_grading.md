# Enterprise Grading

## Purpose

`GET /grading/enterprise-readiness` provides a readiness scorecard using lightweight functional checks in addition to code/config presence.

## Categories and Weights

- Data lifecycle and quality (20)
- Metrics governance (15)
- ML workflow (15)
- Agentic workflow (20)
- RAG implementation (10)
- Reporting (10)
- Operational readiness (10)

Total: 100 points.

## Grade Bands

- A: 90-100
- B: 80-89
- C: 70-79
- D: 60-69
- F: <60

## CLI

Run:

```bash
python scripts/run_enterprise_grade.py
```

Outputs:
- overall score and grade
- category scorecards
- critical gaps
- recommendations

## Notes

The grader is intentionally lightweight and non-destructive. It avoids expensive retraining and external system dependencies while still validating core functional behavior.
