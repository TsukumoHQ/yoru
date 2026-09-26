"""Encryption-at-rest for outbound-webhook signing secrets (vuln-0010, strix
pilot bb1f35fc). Both webhook subsystems (per-user ``routers/webhooks.py``
and the admin/org ``routers/admin/webhooks`` forge-template surface) store a
secret they must read back VERBATIM later to compute an outbound HMAC
signature — so, unlike passwords (one-way scrypt, ``auth/local_provider.py``),
this has to be REVERSIBLE. Fernet (AES-128-CBC + HMAC-SHA256, authenticated)
is the standard reversible choice; ``cryptography`` is already a real
dependency here (``services/signing/dsse.py``).

Key resolution mirrors ``_jwt_secret()``'s env-var-then-persisted-file
pattern (``auth/local_provider.py:90-112``): zero-config for a fresh
self-host install (a key is generated once and persisted under the data
dir), with an explicit env override for prod/multi-instance deployments
where every process must derive the same key.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "WEBHOOK_SECRET_ENCRYPTION_KEY"


def _derive_fernet_key(passphrase: str) -> bytes:
    """Fernet requires a 32-byte urlsafe-base64 key; accept an arbitrary
    passphrase from the env so ops doesn't have to hand-generate one."""
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet_key() -> bytes:
    env = os.getenv(_ENV_VAR, "").strip()
    if env:
        return _derive_fernet_key(env)
    backend_root = Path(__file__).resolve().parents[5]
    key_path = backend_root / "data" / ".webhook_secret_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip().encode("ascii")
    generated = Fernet.generate_key()
    key_path.write_text(generated.decode("ascii"), encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return generated


def encrypt_secret(raw: str) -> str:
    """Encrypt a raw webhook secret for storage. Returns ascii ciphertext."""
    return Fernet(_fernet_key()).encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored webhook secret back to its raw value — only ever
    called on the outbound-delivery signing path, never in an API response."""
    try:
        return Fernet(_fernet_key()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("webhook secret ciphertext is invalid or tampered") from exc


# Every Fernet token starts with the version byte 0x80, which base64-encodes to
# "gAAAA". Legacy plaintext secrets (hex from generate_webhook_secret, or
# "whsec_..." from the per-user router) can never start with it.
_FERNET_PREFIX = "gAAAA"


def is_encrypted(stored: str) -> bool:
    return stored.startswith(_FERNET_PREFIX)


def read_secret(stored: str) -> tuple[str, bool]:
    """Raw signing secret plus whether the row still held it in plaintext
    (created before vuln-0010). A Fernet-shaped value that fails to decrypt
    (wrong or lost key) raises ValueError, it is never mistaken for plaintext."""
    if is_encrypted(stored):
        return decrypt_secret(stored), False
    return stored, True
