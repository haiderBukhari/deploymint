"""Encrypted-at-rest cloud credential storage. See docs/36-monitoring.md.

The first crypto dependency in this codebase. Only `encrypted_blob` (Fernet
ciphertext) ever touches the database — the key comes from
DEPLOYMINT_SECRET_KEY (an env var, see config.py's secret_key property),
never stored next to the data it protects. Never logs a plaintext
credential value, mirroring core/cloud_creds.py's existing discipline.
"""

import json

from cryptography.fernet import Fernet, InvalidToken

from deploymint.config import get_settings
from deploymint.db.database import get_session_factory
from deploymint.db.models import CloudCredential


class SecretKeyMissing(Exception):
    """Raised when DEPLOYMINT_SECRET_KEY isn't set — credential-saving
    endpoints turn this into a clear 400, never silently store with a
    default/weak key."""


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise SecretKeyMissing(
            "DEPLOYMINT_SECRET_KEY is not set — generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" and add it to .env'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise SecretKeyMissing(f"DEPLOYMINT_SECRET_KEY is not a valid Fernet key: {e}") from e


def encrypt(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt(blob: bytes) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob).decode())
    except InvalidToken as e:
        raise SecretKeyMissing(
            "stored credential could not be decrypted — DEPLOYMINT_SECRET_KEY "
            "may have changed since it was saved"
        ) from e


def save_credentials(cloud: str, data: dict) -> None:
    blob = encrypt(data)
    Session = get_session_factory()
    with Session() as db:
        existing = db.query(CloudCredential).filter_by(cloud=cloud).first()
        if existing:
            existing.encrypted_blob = blob
        else:
            db.add(CloudCredential(cloud=cloud, encrypted_blob=blob))
        db.commit()


def load_credentials(cloud: str) -> dict | None:
    Session = get_session_factory()
    with Session() as db:
        row = db.query(CloudCredential).filter_by(cloud=cloud).first()
        if not row:
            return None
        return decrypt(row.encrypted_blob)


def delete_credentials(cloud: str) -> bool:
    Session = get_session_factory()
    with Session() as db:
        row = db.query(CloudCredential).filter_by(cloud=cloud).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True


def credential_status() -> dict[str, dict | None]:
    """{"aws": {"updated_at": "..."} or None, "azure": ..., "gcp": ...} —
    presence/timestamp only, never the decrypted values or even a masked
    version of a secret."""
    Session = get_session_factory()
    with Session() as db:
        rows = {r.cloud: r for r in db.query(CloudCredential).all()}
    return {
        cloud: {"updated_at": rows[cloud].updated_at.isoformat()} if cloud in rows else None
        for cloud in ("aws", "azure", "gcp")
    }
