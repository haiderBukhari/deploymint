"""See docs/21-cloud-deploy.md."""

import pytest

from deploymint.core.cloud_creds import CloudCredentials, MissingCredentials, build_env


def test_aws_builds_expected_env():
    creds = CloudCredentials(aws_access_key_id="AKIA", aws_secret_access_key="secret",
                              aws_region="us-east-1")
    env = build_env("aws", creds)
    assert env == {
        "AWS_ACCESS_KEY_ID": "AKIA",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_DEFAULT_REGION": "us-east-1",
    }


def test_aws_includes_session_token_only_when_present():
    creds = CloudCredentials(aws_access_key_id="AKIA", aws_secret_access_key="secret",
                              aws_region="us-east-1", aws_session_token="tok")
    env = build_env("aws", creds)
    assert env["AWS_SESSION_TOKEN"] == "tok"


def test_aws_missing_fields_raises():
    with pytest.raises(MissingCredentials, match="aws_secret_access_key"):
        build_env("aws", CloudCredentials(aws_access_key_id="AKIA", aws_region="us-east-1"))


def test_azure_builds_expected_env():
    creds = CloudCredentials(azure_subscription_id="sub", azure_tenant_id="tenant",
                              azure_client_id="client", azure_client_secret="secret")
    env = build_env("azure", creds)
    assert env == {
        "ARM_SUBSCRIPTION_ID": "sub",
        "ARM_TENANT_ID": "tenant",
        "ARM_CLIENT_ID": "client",
        "ARM_CLIENT_SECRET": "secret",
    }


def test_azure_missing_fields_raises():
    with pytest.raises(MissingCredentials, match="azure_client_secret"):
        build_env("azure", CloudCredentials(azure_subscription_id="s", azure_tenant_id="t",
                                             azure_client_id="c"))


def test_gcp_builds_expected_env_without_leaking_json_into_env():
    creds = CloudCredentials(gcp_project="proj", gcp_credentials_json='{"type": "service_account"}')
    env = build_env("gcp", creds)
    assert env == {"GOOGLE_PROJECT": "proj"}
    assert "gcp_credentials_json" not in str(env)


def test_gcp_missing_fields_raises():
    with pytest.raises(MissingCredentials, match="gcp_credentials_json"):
        build_env("gcp", CloudCredentials(gcp_project="proj"))


def test_unknown_cloud_raises():
    with pytest.raises(MissingCredentials, match="unknown cloud provider"):
        build_env("digitalocean", CloudCredentials())
