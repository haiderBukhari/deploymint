"""Live AWS Cost Explorer must degrade to None (never raise) on every failure
mode, so the caller always has a working sample-data fallback. See
docs/17-pending-work.md §17.5."""

from unittest.mock import MagicMock, patch

import pytest

from deploymint.core import aws_cost


def test_no_credentials_returns_none(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert aws_cost.credentials_present() is False
    assert aws_cost.fetch_cost_by_service() is None


def test_credentials_present(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    assert aws_cost.credentials_present() is True


def test_boto3_call_failure_returns_none_not_a_crash(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    mock_boto3 = MagicMock()
    mock_boto3.client.side_effect = RuntimeError("no network")
    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        assert aws_cost.fetch_cost_by_service() is None


def test_successful_call_shapes_result_by_service(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    mock_boto3 = MagicMock()
    mock_client = MagicMock()
    mock_client.get_cost_and_usage.return_value = {
        "ResultsByTime": [{
            "Groups": [
                {"Keys": ["Amazon EC2"], "Metrics": {"UnblendedCost": {"Amount": "12.50"}}},
                {"Keys": ["Amazon RDS"], "Metrics": {"UnblendedCost": {"Amount": "7.25"}}},
            ]
        }]
    }
    mock_boto3.client.return_value = mock_client
    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        result = aws_cost.fetch_cost_by_service()
    assert result is not None
    by_service, period = result
    assert by_service == {"Amazon EC2": 12.50, "Amazon RDS": 7.25}
    assert " to " in period


@pytest.mark.asyncio
async def test_doctor_reports_sample_data_by_default(client):
    r = client.get("/api/doctor")
    checks = {c["name"]: c for c in r.json()["checks"]}
    assert checks["cost_source"]["status"] == "warn"
    assert "sample data" in checks["cost_source"]["detail"]
