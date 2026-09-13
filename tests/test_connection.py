"""Offline encrypted-transfer tests using fake credentials and temporary state."""
import base64
import hashlib
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worldfuzz import connection


MOCK_KEY = "mock-key-never-a-real-credential"
MOCK_MODEL = "nvidia/Nemotron-MOCK-only"
NOW = 1_800_000_000


class ConnectionTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "private"
        # Windows OpenSSL needs the OS environment for its secure entropy source.
        environment = {"WORLDFUZZ_CONFIG_DIR": str(self.state)}
        if os.name == "nt" and os.environ.get("SystemRoot"):
            environment["SystemRoot"] = os.environ["SystemRoot"]
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)


class CredentialLoaderTests(ConnectionTestCase):
    def test_missing_credentials_do_not_create_files(self):
        self.assertEqual(connection.load_credentials(), (None, None))
        self.assertFalse(self.state.exists())

    def test_environment_key_skips_private_state_and_optional_crypto(self):
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY, "NEBIUS_MODEL": MOCK_MODEL}), \
             patch.object(connection, "_state_dir", side_effect=AssertionError("Unexpected state lookup")), \
             patch.object(connection, "_crypto", side_effect=AssertionError("Unexpected crypto import")):
            self.assertEqual(connection.load_credentials(), (MOCK_KEY, MOCK_MODEL))

    def test_windows_uses_environment_or_dpapi_launcher_never_posix_file_storage(self):
        with patch.object(connection.os, "name", "nt"):
            self.assertEqual(connection.load_credentials(), (None, None))
            with self.assertRaisesRegex(connection.ConnectionError, "DPAPI"):
                connection._state_dir(create=True)
            with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY}):
                self.assertEqual(connection.load_credentials(), (MOCK_KEY, None))

    def test_invalid_environment_values_are_secret_safe(self):
        for key, model in ((MOCK_KEY + "\n", None), (MOCK_KEY, "other/Nemotron-MOCK")):
            with self.subTest(model=model), patch.dict(os.environ, {"NEBIUS_API_KEY": key,
                                                                    "NEBIUS_MODEL": model or ""}):
                with self.assertRaises(connection.ConnectionError) as captured:
                    connection.load_credentials()
                self.assertNotIn(MOCK_KEY, str(captured.exception))

    def test_private_state_inside_project_is_rejected(self):
        with patch.dict(os.environ, {"WORLDFUZZ_CONFIG_DIR": str(connection.PROJECT_ROOT / "private")}):
            with self.assertRaises(connection.ConnectionError):
                connection._state_dir()

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and permissions")
    def test_group_readable_state_is_rejected(self):
        self.state.mkdir(mode=0o700)
        path = self.state / "credentials.json"
        path.write_text(json.dumps({"api_key": MOCK_KEY, "model": MOCK_MODEL}))
        path.chmod(0o640)
        with self.assertRaises(connection.ConnectionError):
            connection.load_credentials()

    @unittest.skipUnless(os.name == "posix", "POSIX symlink protection")
    def test_private_file_symlink_is_rejected(self):
        self.state.mkdir(mode=0o700)
        real = Path(self.temporary.name) / "other.json"
        real.write_text(json.dumps({"api_key": MOCK_KEY, "model": MOCK_MODEL}))
        real.chmod(0o600)
        (self.state / "credentials.json").symlink_to(real)
        with self.assertRaises(connection.ConnectionError):
            connection.load_credentials()


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "Optional cryptography package is not installed")
@unittest.skipUnless(os.name == "posix", "Receiver private storage requires POSIX permissions")
class EncryptedTransferTests(ConnectionTestCase):
    def setUp(self):
        super().setUp()
        self.request_path = Path(self.temporary.name) / "public-request.json"
        self.envelope_path = Path(self.temporary.name) / "connection.enc.json"

    def prepare(self, key=MOCK_KEY, model=MOCK_MODEL):
        request = connection.create_request(self.request_path, now=NOW)
        with patch.dict(os.environ, {"NEBIUS_API_KEY": key, "NEBIUS_MODEL": model or ""}):
            connection.seal_connection(self.request_path, self.envelope_path, now=NOW)
        return request

    def test_roundtrip_keeps_public_artifacts_secret_free_and_consumes_receiver_key(self):
        request = self.prepare()
        self.assertNotIn(MOCK_KEY, self.request_path.read_text())
        self.assertNotIn("PRIVATE KEY", self.request_path.read_text())
        self.assertNotIn(MOCK_KEY, self.envelope_path.read_text())
        self.assertNotIn(MOCK_MODEL, self.envelope_path.read_text())
        connection.import_connection(self.envelope_path, now=NOW)
        self.assertEqual(connection.load_credentials(), (MOCK_KEY, MOCK_MODEL))
        self.assertFalse((self.state / f"transfer-{request['request_id']}.pem").exists())
        self.assertFalse((self.state / f"transfer-{request['request_id']}.json").exists())
        if os.name == "posix":
            self.assertEqual((self.state / "credentials.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_hybrid_envelope_handles_long_token_and_optional_model(self):
        key = "mock-long-token-" + "x" * 6000
        self.prepare(key=key, model=None)
        connection.import_connection(self.envelope_path, now=NOW)
        self.assertEqual(connection.load_credentials(), (key, None))

    def test_ciphertext_tampering_does_not_save_or_consume_request(self):
        request = self.prepare()
        envelope = json.loads(self.envelope_path.read_text())
        data = bytearray(base64.b64decode(envelope["ciphertext"]))
        data[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(data).decode()
        self.envelope_path.write_text(json.dumps(envelope))
        with self.assertRaises(connection.ConnectionError) as captured:
            connection.import_connection(self.envelope_path, now=NOW)
        self.assertNotIn(MOCK_KEY, str(captured.exception))
        self.assertFalse((self.state / "credentials.json").exists())
        self.assertTrue((self.state / f"transfer-{request['request_id']}.pem").exists())

    def test_expired_request_cannot_be_sealed_or_imported(self):
        self.prepare()
        expired = NOW + connection.REQUEST_LIFETIME_SECONDS
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY}):
            with self.assertRaises(connection.ConnectionError):
                connection.seal_connection(self.request_path, Path(self.temporary.name) / "expired.json", now=expired)
        with self.assertRaises(connection.ConnectionError):
            connection.import_connection(self.envelope_path, now=expired)
        self.assertFalse((self.state / "credentials.json").exists())

    def test_consumed_envelope_cannot_be_replayed_even_with_replace(self):
        self.prepare()
        connection.import_connection(self.envelope_path, now=NOW)
        with self.assertRaises(connection.ConnectionError):
            connection.import_connection(self.envelope_path, replace=True, now=NOW)
        self.assertEqual(connection.load_credentials(), (MOCK_KEY, MOCK_MODEL))

    def test_existing_credentials_require_explicit_replace(self):
        self.prepare()
        connection.import_connection(self.envelope_path, now=NOW)
        self.request_path = Path(self.temporary.name) / "second-request.json"
        self.envelope_path = Path(self.temporary.name) / "second.enc.json"
        self.prepare(key="different-mock-key")
        with self.assertRaises(connection.ConnectionError):
            connection.import_connection(self.envelope_path, now=NOW)
        self.assertEqual(connection.load_credentials(), (MOCK_KEY, MOCK_MODEL))
        connection.import_connection(self.envelope_path, replace=True, now=NOW)
        self.assertEqual(connection.load_credentials(), ("different-mock-key", MOCK_MODEL))

    def test_saved_model_can_be_overridden_by_environment(self):
        self.prepare()
        connection.import_connection(self.envelope_path, now=NOW)
        with patch.dict(os.environ, {"NEBIUS_MODEL": "nvidia/Nemotron-SECOND-MOCK"}):
            self.assertEqual(connection.load_credentials(), (MOCK_KEY, "nvidia/Nemotron-SECOND-MOCK"))

    def test_request_fingerprint_and_envelope_identity_are_checked(self):
        self.prepare()
        envelope = json.loads(self.envelope_path.read_text())
        envelope["public_key_sha256"] = "0" * 64
        self.envelope_path.write_text(json.dumps(envelope))
        with self.assertRaises(connection.ConnectionError):
            connection.import_connection(self.envelope_path, now=NOW)
        request = json.loads(self.request_path.read_text())
        request["public_key_sha256"] = "0" * 64
        self.request_path.write_text(json.dumps(request))
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY}):
            with self.assertRaises(connection.ConnectionError):
                connection.seal_connection(self.request_path, Path(self.temporary.name) / "mismatch.json", now=NOW)

    def test_untrusted_request_id_cannot_select_a_path(self):
        self.prepare()
        envelope = json.loads(self.envelope_path.read_text())
        envelope["request_id"] = "../credentials"
        self.envelope_path.write_text(json.dumps(envelope))
        with self.assertRaises(connection.ConnectionError):
            connection.import_connection(self.envelope_path, now=NOW)

    def test_input_size_limit_and_duplicate_fields(self):
        self.request_path.write_bytes(b" " * (connection.MAX_FILE_BYTES + 1))
        with self.assertRaises(connection.ConnectionError):
            connection.seal_connection(self.request_path, self.envelope_path, now=NOW)
        self.request_path.write_text('{"version":1,"version":1}')
        with self.assertRaises(connection.ConnectionError):
            connection.seal_connection(self.request_path, self.envelope_path, now=NOW)

    def test_cli_reports_no_secret_on_success_or_failure(self):
        connection.create_request(self.request_path, now=NOW)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(connection.time, "time", return_value=NOW), \
             patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY, "NEBIUS_MODEL": MOCK_MODEL}), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = connection.main(["seal", "--request", str(self.request_path), "--out", str(self.envelope_path)])
        self.assertEqual(code, 0)
        self.assertNotIn(MOCK_KEY, stdout.getvalue() + stderr.getvalue())
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY + "\n"}), \
             patch.object(connection.time, "time", return_value=NOW), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = connection.main(["seal", "--request", str(self.request_path), "--out", str(self.envelope_path)])
        self.assertEqual(code, 2)
        self.assertNotIn(MOCK_KEY, stdout.getvalue() + stderr.getvalue())


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "Optional cryptography package is not installed")
class SenderTests(ConnectionTestCase):
    """Exercise the Windows sender without a POSIX receiver store."""

    def setUp(self):
        super().setUp()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        public = self.private.public_key()
        self.request = {
            "version": 1, "algorithm": connection.ALGORITHM,
            "request_id": "a" * 32, "created_at": NOW,
            "expires_at": NOW + connection.REQUEST_LIFETIME_SECONDS,
            "public_key_pem": public.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("ascii"),
            "public_key_sha256": hashlib.sha256(public.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )).hexdigest(),
        }
        self.request_path = Path(self.temporary.name) / "public-request.json"
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.envelope_path = Path(self.temporary.name) / "connection.enc.json"

    def test_sender_encrypts_key_and_model_without_private_file_storage(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY, "NEBIUS_MODEL": MOCK_MODEL}), \
             patch.object(connection, "_state_dir", side_effect=AssertionError("Unexpected private storage")):
            connection.seal_connection(self.request_path, self.envelope_path, now=NOW)
        text = self.envelope_path.read_text()
        self.assertNotIn(MOCK_KEY, text)
        self.assertNotIn(MOCK_MODEL, text)
        envelope = json.loads(text)
        symmetric = self.private.decrypt(base64.b64decode(envelope["wrapped_key"]), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        header = {name: envelope[name] for name in ("version", "algorithm", "request_id", "public_key_sha256")}
        plaintext = AESGCM(symmetric).decrypt(
            base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["ciphertext"]),
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        self.assertEqual(json.loads(plaintext), {"api_key": MOCK_KEY, "model": MOCK_MODEL})
        self.assertFalse(self.state.exists())

    def test_sender_rejects_expired_request_without_output(self):
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY}):
            with self.assertRaisesRegex(connection.ConnectionError, "expired"):
                connection.seal_connection(self.request_path, self.envelope_path, now=self.request["expires_at"])
        self.assertFalse(self.envelope_path.exists())

    def test_sender_does_not_overwrite_existing_envelope(self):
        self.envelope_path.write_bytes(b"previous-envelope")
        with patch.dict(os.environ, {"NEBIUS_API_KEY": MOCK_KEY}):
            with self.assertRaises(connection.ConnectionError):
                connection.seal_connection(self.request_path, self.envelope_path, now=NOW)
        self.assertEqual(self.envelope_path.read_bytes(), b"previous-envelope")


if __name__ == "__main__":
    unittest.main()
