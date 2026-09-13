"""One-use encrypted transfer of a user's own Nebius connection.

The public request and sealed envelope may be shared. Private keys and imported
credentials stay outside the project. No network operations occur here.
Cryptography is an optional dependency, needed only for the transfer commands.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time


MAX_FILE_BYTES = 65_536
REQUEST_LIFETIME_SECONDS = 7 * 24 * 60 * 60
ALGORITHM = "RSA3072-OAEP-SHA256+A256GCM"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConnectionError(RuntimeError):
    """Messages contain neither credentials nor untrusted file contents."""


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise ConnectionError(
            "Encrypted transfer requires the optional cryptography dependency."
        ) from None
    return hashes, serialization, padding, rsa, AESGCM


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _json(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    def reject(_):
        raise ValueError("Nonfinite value")

    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError):
        raise ConnectionError("Connection file contains invalid JSON.") from None


def _check_private(info, directory=False):
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode):
        raise ConnectionError("Private state must use regular files and directories.")
    if os.name == "posix" and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ConnectionError("Private state must be owned by this user with owner-only permissions.")


def _state_dir(create=False) -> Path:
    if os.name != "posix":
        raise ConnectionError("Private file storage requires POSIX permissions. On Windows, use the DPAPI launcher.")
    configured = os.environ.get("WORLDFUZZ_CONFIG_DIR")
    path = Path(configured).expanduser() if configured else Path.home() / ".local/share/worldfuzz"
    if not path.is_absolute():
        raise ConnectionError("WORLDFUZZ_CONFIG_DIR must be an absolute path outside the project.")
    if path.resolve().is_relative_to(PROJECT_ROOT):
        raise ConnectionError("Private connection state must stay outside the project.")
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            _check_private(path.lstat(), directory=True)
    except OSError:
        raise ConnectionError("Private connection directory is unavailable.") from None
    return path


def _read(path: Path, private=False) -> bytes:
    try:
        flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0) if private else 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if private:
                _check_private(info)
            elif not stat.S_ISREG(info.st_mode):
                raise ConnectionError("Connection input must be a regular file.")
            if info.st_size > MAX_FILE_BYTES:
                raise ConnectionError("Connection file exceeds the size limit.")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ConnectionError("Connection file exceeds the size limit.")
        return data
    except OSError:
        raise ConnectionError("Connection file is unavailable.") from None


def _write(path: Path, data: bytes, *, private=False, replace=False):
    """Exclusive creation by default; atomic replacement only when requested."""
    if len(data) > MAX_FILE_BYTES:
        raise ConnectionError("Connection file exceeds the size limit.")
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(8)) if replace else path
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            if path.exists() or path.is_symlink():
                _check_private(path.lstat())
            os.replace(temporary, path)
        if private:
            _check_private(path.lstat())
    except (OSError, ConnectionError):
        if created and temporary.exists():
            temporary.unlink(missing_ok=True)
        raise ConnectionError("Connection output could not be saved; existing files are not overwritten by default.") from None


def _credentials(value):
    if not isinstance(value, dict) or set(value) != {"api_key", "model"}:
        raise ConnectionError("Connection credentials have an invalid structure.")
    key, model = value["api_key"], value["model"]
    if not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,16384}", key):
        raise ConnectionError("A nonempty API key without whitespace is required.")
    if model is not None and (not isinstance(model, str) or len(model) > 200 or not re.fullmatch(
        r"nvidia/[A-Za-z0-9_.-]*nemotron[A-Za-z0-9_.-]*", model, re.IGNORECASE
    )):
        raise ConnectionError("The selected model must be an NVIDIA Nemotron model identifier.")
    return key, model


def load_credentials() -> tuple[str | None, str | None]:
    """Environment key takes precedence without reading private files.

    If no key is in the environment, use the owner-only imported file. An
    explicitly supplied model overrides the saved model. Missing state returns
    a missing key; malformed or insecure state fails closed.
    """
    key = os.environ.get("NEBIUS_API_KEY")
    model = os.environ.get("NEBIUS_MODEL") or None
    if key:
        return _credentials({"api_key": key, "model": model})
    if model is not None:
        _credentials({"api_key": "validation-placeholder", "model": model})
    # Windows launchers supply DPAPI credentials through the child environment.
    # Missing configuration must remain diagnosable without POSIX file access.
    if os.name == "nt":
        return None, model
    path = _state_dir() / "credentials.json"
    if not path.exists() and not path.is_symlink():
        return None, model
    saved_key, saved_model = _credentials(_json(_read(path, private=True)))
    return saved_key, model if model is not None else saved_model


def _request(value, now: int):
    fields = {"version", "algorithm", "request_id", "created_at", "expires_at",
              "public_key_pem", "public_key_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ConnectionError("Public connection request has an invalid structure.")
    if (type(value["version"]) is not int or value["version"] != 1
        or value["algorithm"] != ALGORITHM
        or not isinstance(value["request_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["request_id"])
        or type(value["created_at"]) is not int or type(value["expires_at"]) is not int
        or value["expires_at"] - value["created_at"] != REQUEST_LIFETIME_SECONDS
        or not isinstance(value["public_key_pem"], str)
        or not isinstance(value["public_key_sha256"], str)):
        raise ConnectionError("Public connection request contains invalid metadata.")
    if now < value["created_at"] - 300 or now >= value["expires_at"]:
        raise ConnectionError("Connection request is expired or not yet valid.")
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    from cryptography.exceptions import UnsupportedAlgorithm
    try:
        public = serialization.load_pem_public_key(value["public_key_pem"].encode("ascii"))
        if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072:
            raise ValueError("Unsupported public key")
        der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if hashlib.sha256(der).hexdigest() != value["public_key_sha256"]:
            raise ValueError("Public key fingerprint mismatch")
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        raise ConnectionError("Public connection request has an invalid public key.") from None
    return public


def create_request(out: str | Path, *, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    directory = _state_dir(create=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = private.public_key()
    request_id = secrets.token_hex(16)
    request = {
        "version": 1, "algorithm": ALGORITHM, "request_id": request_id,
        "created_at": now, "expires_at": now + REQUEST_LIFETIME_SECONDS,
        "public_key_pem": public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )).hexdigest(),
    }
    private_path = directory / f"transfer-{request_id}.pem"
    metadata_path = directory / f"transfer-{request_id}.json"
    created = []
    try:
        _write(private_path, private.private_bytes(serialization.Encoding.PEM,
               serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), private=True)
        created.append(private_path)
        _write(metadata_path, _canonical(request), private=True)
        created.append(metadata_path)
        _write(Path(out), _canonical(request))
    except ConnectionError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return request


def _header(request):
    return {name: request[name] for name in ("version", "algorithm", "request_id", "public_key_sha256")}


def seal_connection(request_path: str | Path, out: str | Path, *, now: int | None = None) -> None:
    """Read the sender's key from its process environment and write ciphertext."""
    now = int(time.time()) if now is None else now
    request = _json(_read(Path(request_path)))
    public = _request(request, now)
    key, model = _credentials({"api_key": os.environ.get("NEBIUS_API_KEY"),
                               "model": os.environ.get("NEBIUS_MODEL") or None})
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    symmetric = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(12)
    header = _header(request)
    wrapped = public.encrypt(symmetric, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                    algorithm=hashes.SHA256(), label=None))
    ciphertext = AESGCM(symmetric).encrypt(nonce, _canonical({"api_key": key, "model": model}),
                                         _canonical(header))
    envelope = {**header, "wrapped_key": base64.b64encode(wrapped).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii")}
    _write(Path(out), _canonical(envelope))


def import_connection(envelope_path: str | Path, *, replace=False, now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    envelope = _json(_read(Path(envelope_path)))
    fields = {"version", "algorithm", "request_id", "public_key_sha256", "wrapped_key", "nonce", "ciphertext"}
    if (not isinstance(envelope, dict) or set(envelope) != fields
        or type(envelope["version"]) is not int or envelope["version"] != 1
        or envelope["algorithm"] != ALGORITHM
        or not isinstance(envelope["request_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", envelope["request_id"])):
        raise ConnectionError("Encrypted connection has invalid metadata.")
    directory = _state_dir()
    credential_path = directory / "credentials.json"
    if (credential_path.exists() or credential_path.is_symlink()) and not replace:
        raise ConnectionError("A connection already exists. Use --replace to replace it explicitly.")
    request_id = envelope["request_id"]
    private_path = directory / f"transfer-{request_id}.pem"
    metadata_path = directory / f"transfer-{request_id}.json"
    request = _json(_read(metadata_path, private=True))
    _request(request, now)
    if _header(envelope) != _header(request):
        raise ConnectionError("Encrypted connection does not match this request.")
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    try:
        private = serialization.load_pem_private_key(_read(private_path, private=True), password=None)
        if not isinstance(private, rsa.RSAPrivateKey) or private.key_size != 3072:
            raise ValueError("Invalid private key")
        fingerprint = hashlib.sha256(private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        if fingerprint != request["public_key_sha256"]:
            raise ValueError("Mismatched private key")
        wrapped, nonce, ciphertext = (base64.b64decode(envelope[name], validate=True)
                                      for name in ("wrapped_key", "nonce", "ciphertext"))
        if len(wrapped) != 384 or len(nonce) != 12 or len(ciphertext) < 16:
            raise ValueError("Invalid encryption parameters")
        symmetric = private.decrypt(wrapped, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                          algorithm=hashes.SHA256(), label=None))
        if len(symmetric) != 32:
            raise ValueError("Invalid encryption key")
        plaintext = AESGCM(symmetric).decrypt(nonce, ciphertext, _canonical(_header(request)))
    except ConnectionError:
        raise
    except Exception:
        # Includes authentication-tag failures. Never expose provider secrets or
        # the library's exception details for malformed attacker-controlled data.
        raise ConnectionError("Encrypted connection could not be authenticated or decrypted.") from None
    key, model = _credentials(_json(plaintext))
    _write(credential_path, _canonical({"api_key": key, "model": model}), private=True, replace=replace)
    try:
        private_path.unlink()
        metadata_path.unlink()
    except OSError:
        raise ConnectionError("Connection was saved but one-use transfer state could not be removed.") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="Create a public request and a private receiver key")
    request.add_argument("--out", required=True)
    seal = commands.add_parser("seal", help="Encrypt the connection supplied by environment variables")
    seal.add_argument("--request", required=True)
    seal.add_argument("--out", required=True)
    receive = commands.add_parser("import", help="Import a sealed connection into private local state")
    receive.add_argument("--envelope", required=True)
    receive.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "request":
            create_request(args.out)
        elif args.command == "seal":
            seal_connection(args.request, args.out)
        else:
            import_connection(args.envelope, replace=args.replace)
        result = {"status": "saved", "command": args.command}
        if args.command != "import":
            result["output"] = str(Path(args.out).absolute())
        print(json.dumps(result))
        return 0
    except ConnectionError as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
