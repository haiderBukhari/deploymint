"""Encrypted-at-rest cloud credential storage. See docs/36-monitoring.md."""

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from deploymint.core import credential_store as cs

_KEY = Fernet.generate_key().decode()


class _WithKey:
    secret_key = _KEY


class _NoKey:
    secret_key = ""


def test_encrypt_decrypt_round_trip():
    with patch("deploymint.core.credential_store.get_settings", return_value=_WithKey()):
        blob = cs.encrypt({"a": "1", "b": "2"})
        assert isinstance(blob, bytes)
        assert cs.decrypt(blob) == {"a": "1", "b": "2"}


def test_encrypt_never_stores_plaintext_recognizably():
    with patch("deploymint.core.credential_store.get_settings", return_value=_WithKey()):
        blob = cs.encrypt({"aws_secret_access_key": "super-secret-value"})
    assert b"super-secret-value" not in blob


def test_missing_secret_key_raises_secret_key_missing():
    with patch("deploymint.core.credential_store.get_settings", return_value=_NoKey()):
        with pytest.raises(cs.SecretKeyMissing):
            cs.encrypt({"a": "1"})


def test_invalid_secret_key_raises_secret_key_missing():
    class _BadKey:
        secret_key = "not-a-valid-fernet-key"

    with patch("deploymint.core.credential_store.get_settings", return_value=_BadKey()):
        with pytest.raises(cs.SecretKeyMissing):
            cs.encrypt({"a": "1"})


def test_save_load_delete_round_trip(workspace):
    with patch("deploymint.core.credential_store.get_settings", return_value=_WithKey()):
        assert cs.load_credentials("aws") is None
        cs.save_credentials("aws", {"aws_access_key_id": "AKIA123"})
        assert cs.load_credentials("aws") == {"aws_access_key_id": "AKIA123"}

        # Saving again replaces, doesn't duplicate.
        cs.save_credentials("aws", {"aws_access_key_id": "AKIA456"})
        assert cs.load_credentials("aws") == {"aws_access_key_id": "AKIA456"}

        assert cs.delete_credentials("aws") is True
        assert cs.load_credentials("aws") is None
        assert cs.delete_credentials("aws") is False


def test_credential_status_reports_presence_and_timestamp_only(workspace):
    with patch("deploymint.core.credential_store.get_settings", return_value=_WithKey()):
        cs.save_credentials("gcp", {"gcp_project": "my-proj"})
        status = cs.credential_status()
    assert status["aws"] is None
    assert status["azure"] is None
    assert status["gcp"] is not None
    assert "updated_at" in status["gcp"]
    assert "gcp_project" not in status["gcp"]  # never the decrypted value
