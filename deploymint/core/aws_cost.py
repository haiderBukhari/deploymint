"""Live AWS Cost Explorer connection — a source swap for the sample JSON
export in api/costs.py, not a rewrite, because the sample was deliberately
shaped to match the real API's response. See docs/17-pending-work.md §17.5.

boto3 is an optional dependency (pyproject.toml's [aws] extra) — installing
it plus setting AWS credentials is what turns this source on. Every failure
mode here returns None rather than raising, so callers always have a
working fallback (the same resilience pattern as the LLM and security
scanner paths): no credentials, boto3 not installed, a network error, or an
empty result all just mean "use the sample data instead."
"""

import os
from datetime import date, timedelta


def credentials_present() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))


def fetch_cost_by_service(days: int = 30) -> tuple[dict[str, float], str] | None:
    if not credentials_present():
        return None
    try:
        import boto3
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=days)
    try:
        client = boto3.client("ce", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        resp = client.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
    except Exception:
        return None

    by_service: dict[str, float] = {}
    for result in resp.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            svc = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            by_service[svc] = by_service.get(svc, 0.0) + amount

    if not by_service:
        return None
    return by_service, f"{start.isoformat()} to {end.isoformat()}"
