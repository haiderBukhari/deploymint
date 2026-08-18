"""Maps the credential fields a user pastes in the browser to the exact env
vars Terraform's AWS/GCP/Azure providers read. See docs/21-cloud-deploy.md.

Nothing here writes credentials to disk or a database — build_env() returns
a plain dict that the caller merges into a single subprocess call's env and
then discards. This module only validates shape; it never logs or persists
a credential value.
"""

from dataclasses import dataclass


class MissingCredentials(ValueError):
    """Raised when a cloud's required fields are absent. The message lists
    field names only, never values, so it's always safe to show in the UI."""


@dataclass(frozen=True)
class CloudCredentials:
    # aws
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = ""
    # azure
    azure_subscription_id: str = ""
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    # gcp
    gcp_project: str = ""
    gcp_credentials_json: str = ""


_REQUIRED = {
    "aws": ["aws_access_key_id", "aws_secret_access_key", "aws_region"],
    "azure": ["azure_subscription_id", "azure_tenant_id", "azure_client_id", "azure_client_secret"],
    "gcp": ["gcp_project", "gcp_credentials_json"],
}


def build_env(cloud: str, creds: CloudCredentials) -> dict[str, str]:
    """Returns the env vars Terraform's provider blocks read natively for
    `cloud` (no provider-specific config files needed). Raises
    MissingCredentials if a required field is blank."""
    required = _REQUIRED.get(cloud)
    if required is None:
        raise MissingCredentials(f"unknown cloud provider: {cloud}")

    missing = [f for f in required if not getattr(creds, f)]
    if missing:
        raise MissingCredentials(f"missing required fields for {cloud}: {', '.join(missing)}")

    if cloud == "aws":
        env = {
            "AWS_ACCESS_KEY_ID": creds.aws_access_key_id,
            "AWS_SECRET_ACCESS_KEY": creds.aws_secret_access_key,
            "AWS_DEFAULT_REGION": creds.aws_region,
        }
        if creds.aws_session_token:
            env["AWS_SESSION_TOKEN"] = creds.aws_session_token
        return env

    if cloud == "azure":
        return {
            "ARM_SUBSCRIPTION_ID": creds.azure_subscription_id,
            "ARM_TENANT_ID": creds.azure_tenant_id,
            "ARM_CLIENT_ID": creds.azure_client_id,
            "ARM_CLIENT_SECRET": creds.azure_client_secret,
        }

    # gcp — the provider reads a service-account JSON from a file path, not
    # inline; the JSON itself never touches an env var (some shells log
    # env on crash) or the DB. Caller writes it to a private tempfile that's
    # deleted as soon as the terraform process exits.
    return {"GOOGLE_PROJECT": creds.gcp_project}
