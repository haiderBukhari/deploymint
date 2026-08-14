"""FinOps must compute costs deterministically from the Deployment manifest —
no LLM involved at all. See docs/10-phase-6-finops-ui.md §6.3."""

import pytest

from deploymint.agents.finops import FinOpsAgent, load_rate_card, parse_quantity


def test_parse_quantity_cpu():
    assert parse_quantity("500m", "cpu") == 0.5
    assert parse_quantity("2", "cpu") == 2.0
    assert parse_quantity(None, "cpu") == 0.0


def test_parse_quantity_memory():
    assert parse_quantity("512Mi", "mem") == 0.5
    assert parse_quantity("1Gi", "mem") == 1.0


def test_load_rate_card_has_aws_defaults():
    rates = load_rate_card("aws")
    assert rates["vcpu_hour"] > 0
    assert rates["gb_hour"] > 0


DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: t
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: t
        resources:
          requests: {cpu: "500m", memory: "512Mi"}
          limits: {cpu: "4", memory: "512Mi"}
"""


@pytest.mark.asyncio
async def test_estimate_computes_a_nonzero_monthly_cost():
    out = await FinOpsAgent().run(
        {"artifacts": {"k8s_deployment": DEPLOYMENT}, "project_name": "t", "errors": []}
    )
    report = out["cost"]
    assert report["monthly_usd"] > 0
    assert "t" in report["breakdown"]
    # 4x CPU limit-to-request ratio should trigger the over-provisioned warning
    assert any("over-provisioned" in r or "unbounded" in r for r in report["recommendations"])


@pytest.mark.asyncio
async def test_malformed_manifest_degrades_to_zero_cost_not_a_crash():
    out = await FinOpsAgent().run(
        {"artifacts": {"k8s_deployment": "not: valid: yaml: at: all: {"}, "errors": []}
    )
    assert out["cost"]["monthly_usd"] == 0.0
    assert out["cost"]["recommendations"]
