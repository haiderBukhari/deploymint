"""POST/DELETE/GET /api/settings/credentials. See docs/36-monitoring.md."""

from unittest.mock import patch

from cryptography.fernet import Fernet

_KEY = Fernet.generate_key().decode()


def test_get_credential_status_all_unconfigured(client):
    r = client.get("/api/settings/credentials")
    assert r.status_code == 200
    assert r.json() == {"aws": None, "azure": None, "gcp": None}


def test_save_credential_requires_secret_key(client):
    with patch.dict("os.environ", {"DEPLOYMINT_SECRET_KEY": ""}):
        r = client.post("/api/settings/credentials/aws", json={"aws_access_key_id": "AKIA1"})
    assert r.status_code == 400
    assert "DEPLOYMINT_SECRET_KEY" in r.json()["detail"]


def test_save_then_status_then_forget(client):
    with patch.dict("os.environ", {"DEPLOYMINT_SECRET_KEY": _KEY}):
        r = client.post("/api/settings/credentials/aws",
                        json={"aws_access_key_id": "AKIA1", "aws_secret_access_key": "s3cr3t",
                              "aws_region": "us-east-1"})
        assert r.status_code == 201

        status = client.get("/api/settings/credentials").json()
        assert status["aws"] is not None
        assert "updated_at" in status["aws"]

        r = client.delete("/api/settings/credentials/aws")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

        status = client.get("/api/settings/credentials").json()
        assert status["aws"] is None


def test_unknown_cloud_404s(client):
    r = client.post("/api/settings/credentials/bitcoin", json={})
    assert r.status_code == 404
