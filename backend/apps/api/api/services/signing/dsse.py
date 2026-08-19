"""DSSE / Ed25519 signing — wire-compatible with dokan's receipt scheme.

yoru signs an export bundle so a third party can verify OFFLINE, with the
deployer's PUBLIC key only, that the bundle was produced by this instance and
has not been altered. The envelope + PAE + Ed25519 encoding match dokan's
receipt format byte-for-byte (dokan/src/receipt.rs) so the same verifier — or
`dokan verify` — validates a yoru bundle and vice-versa. Frozen spec: project
memory ``yoru-dokan-dsse-wire-format``.

Key material NEVER touches the repo or a transcript: the private signing seed
comes from the ``YORU_SIGNING_KEY`` env (or a keyfile the deployer owns). This
module reads it; it never logs or returns the private bytes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# DSSE payloadType bound into the PAE preamble (must match dokan exactly).
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
# in-toto predicateType for a yoru audit-export statement.
PREDICATE_TYPE = "https://yoru.sh/AuditExport/v1"

_ENV_KEY = "YORU_SIGNING_KEY"


def dsse_pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding — the exact bytes that get signed.

    ``b"DSSEv1 " + len(type) + b" " + type + b" " + len(payload) + b" " + payload``
    where lengths are ASCII decimal of the UTF-8 BYTE length and each SP is a
    single 0x20. Byte-identical to dokan's ``dsse_pae`` so signatures made by
    either side verify under the other.
    """
    type_bytes = payload_type.encode("utf-8")
    return b"".join(
        [
            b"DSSEv1 ",
            str(len(type_bytes)).encode("ascii"),
            b" ",
            type_bytes,
            b" ",
            str(len(payload)).encode("ascii"),
            b" ",
            payload,
        ]
    )


def _ed_public_b64(pub: Ed25519PublicKey) -> str:
    return base64.standard_b64encode(pub.public_bytes_raw()).decode("ascii")


def _ed_keyid(pub: Ed25519PublicKey) -> str:
    """Short stable key id: first 16 hex of SHA-256 over the raw public key."""
    return hashlib.sha256(pub.public_bytes_raw()).hexdigest()[:16]


def ed_sign(private: Ed25519PrivateKey, msg: bytes) -> str:
    """Ed25519 signature as lowercase hex of the 64-byte signature over ``msg``."""
    return private.sign(msg).hex()


def ed_verify(
    public_b64: str, payload_type: str, payload: bytes, sig_hex: str
) -> bool:
    """Verify a DSSE/Ed25519 signature with the PUBLIC key only — the offline,
    third-party path. ``payload`` is the RAW (decoded) statement bytes; the PAE
    is reconstructed here. Returns False on any malformed input (never raises).
    """
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            base64.standard_b64decode(public_b64)
        )
        sig = bytes.fromhex(sig_hex)
    except Exception:
        return False
    if len(sig) != 64:
        return False
    try:
        pub.verify(sig, dsse_pae(payload_type, payload))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


@dataclass(frozen=True)
class SigningKey:
    """A loaded Ed25519 signing key. Holds the private key but exposes only the
    public material + a ``sign`` method — the private bytes never leave here."""

    _private: Ed25519PrivateKey

    @property
    def public_b64(self) -> str:
        return _ed_public_b64(self._private.public_key())

    @property
    def keyid(self) -> str:
        return _ed_keyid(self._private.public_key())

    def sign(self, msg: bytes) -> str:
        return ed_sign(self._private, msg)


def generate_seed_b64() -> str:
    """Keygen helper: a fresh 32-byte Ed25519 seed, base64-std. The DEPLOYER
    stores this as ``YORU_SIGNING_KEY`` and NEVER commits it."""
    seed = Ed25519PrivateKey.generate().private_bytes_raw()
    return base64.standard_b64encode(seed).decode("ascii")


def key_from_seed_b64(seed_b64: str) -> SigningKey:
    """Build a signing key from a base64 32-byte Ed25519 seed."""
    seed = base64.standard_b64decode(seed_b64)
    if len(seed) != 32:
        raise ValueError("YORU_SIGNING_KEY must be a base64 32-byte Ed25519 seed")
    return SigningKey(Ed25519PrivateKey.from_private_bytes(seed))


def load_signing_key() -> SigningKey | None:
    """Load the instance signing key from ``YORU_SIGNING_KEY`` — a base64
    32-byte seed, or a path to a file holding one. Returns None when unset or
    malformed so the caller decides whether an unsigned export is acceptable.
    NEVER logs the key material."""
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return None
    if os.path.isfile(raw):
        try:
            with open(raw, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            return None
    try:
        return key_from_seed_b64(raw)
    except Exception:
        return None


def build_envelope(statement: dict, key: SigningKey) -> dict:
    """Wrap an in-toto statement in a DSSE envelope with the embedded public
    key, matching dokan's receipt shape. The signature is over the PAE of the
    canonical (sorted-key, compact) statement bytes; those exact bytes are also
    what the envelope carries base64-encoded, so a verifier signs/checks the
    SAME bytes it decodes — no re-serialization mismatch.
    """
    payload_bytes = json.dumps(
        statement, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sig_hex = key.sign(dsse_pae(DSSE_PAYLOAD_TYPE, payload_bytes))
    return {
        "dsse": {
            "payloadType": DSSE_PAYLOAD_TYPE,
            "payload": base64.standard_b64encode(payload_bytes).decode("ascii"),
            "signatures": [{"keyid": key.keyid, "sig": sig_hex}],
        },
        "ed25519": {"public_key": key.public_b64, "keyid": key.keyid},
    }


def verify_envelope(envelope: dict) -> bool:
    """Offline, key-free verify of a bundle envelope: reconstruct the PAE from
    the embedded payload + payloadType and check the signature against the
    embedded public key. Tamper-EVIDENT only — a caller enforcing authenticity
    must additionally pin the public-key fingerprint out-of-band (the embedded
    key is taken on faith otherwise). Returns False on any malformed envelope.
    """
    try:
        dsse = envelope["dsse"]
        public_b64 = envelope["ed25519"]["public_key"]
        payload_type = dsse.get("payloadType", DSSE_PAYLOAD_TYPE)
        payload = base64.standard_b64decode(dsse["payload"])
        sig_hex = dsse["signatures"][0]["sig"]
    except Exception:
        return False
    if not payload:
        return False
    return ed_verify(public_b64, payload_type, payload, sig_hex)
