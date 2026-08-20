"""System-wide settings: encrypted cloud credentials for the Monitoring page.
See docs/36-monitoring.md."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deploymint.core.credential_store import (
    SecretKeyMissing,
    credential_status,
    delete_credentials,
    save_credentials,
)

router = APIRouter(tags=["settings"])

_CLOUDS = ("aws", "azure", "gcp")


class CredentialRequest(BaseModel):
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = ""
    azure_subscription_id: str = ""
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    gcp_project: str = ""
    gcp_credentials_json: str = ""


@router.get("/api/settings/credentials")
def get_credential_status():
    """Presence + last-updated timestamp per cloud — never the decrypted
    values, never even a masked secret."""
    return credential_status()


@router.post("/api/settings/credentials/{cloud}", status_code=201)
def save_credential(cloud: str, body: CredentialRequest):
    if cloud not in _CLOUDS:
        raise HTTPException(404, f"unknown cloud: {cloud}")
    try:
        save_credentials(cloud, body.model_dump())
    except SecretKeyMissing as e:
        raise HTTPException(400, str(e)) from e
    return {"cloud": cloud, "status": "saved"}


@router.delete("/api/settings/credentials/{cloud}")
def forget_credential(cloud: str):
    if cloud not in _CLOUDS:
        raise HTTPException(404, f"unknown cloud: {cloud}")
    deleted = delete_credentials(cloud)
    return {"cloud": cloud, "status": "deleted" if deleted else "not_found"}
