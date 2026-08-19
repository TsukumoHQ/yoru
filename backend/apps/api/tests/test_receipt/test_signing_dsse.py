"""M5b-1: DSSE / Ed25519 signer — wire-compatibility with dokan's receipt scheme.

The correctness proof is cross-verification with dokan (dokan/src/receipt.rs):
a bundle signed by yoru-python must verify under dokan-rust's ed_verify, and a
dokan receipt must verify under this Python verifier. Ed25519 is deterministic
(RFC 8032), so a fixed seed + fixed message yields a fixed signature on BOTH
sides — the frozen KAT below is that anchor. dokan-lane confirms the same
(seed, payload) → SIG out-of-band (see YORU_DOKAN_KAT docstring).
"""
from __future__ import annotations

import base64
import json

from apps.api.api.services.signing import dsse

# ── Frozen known-answer test (KAT) ──────────────────────────────────────────
# Fixed 32-byte seed 0x00..0x1f. These are yoru-python's outputs; because the
# scheme is standard Ed25519 + dokan's exact PAE, dokan's ed_sign over the same
# PAE MUST produce SIG byte-for-byte, and dokan's ed_verify MUST accept it.
# CROSS-LANE HANDSHAKE (dokan-lane): assert
#   ed_sign(seed, dsse_pae(DSSE_PAYLOAD_TYPE, PAYLOAD)) == SIG   and
#   ed_verify(PUB_B64, DSSE_PAYLOAD_TYPE, PAYLOAD, SIG) == true
YORU_DOKAN_KAT = {
    "seed_b64": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
    "pub_b64": "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=",
    "keyid": "56475aa75463474c",
    "payload": b'{"hello":"yoru"}',
    "sig": (
        "bf8922bde24cf08efac18ca925941731b702bc3b1166989e8edffcddf2b081a6"
        "ddc36aa9c9dbe00b4a118d02a6d60173488994636d004af0c288659cc106fe0b"
    ),
}


def test_pae_is_byte_exact_to_spec():
    # Hand-computed literal: type is 28 bytes, payload b"{}" is 2 bytes.
    assert dsse.dsse_pae("application/vnd.in-toto+json", b"{}") == (
        b"DSSEv1 28 application/vnd.in-toto+json 2 {}"
    )


def test_pae_uses_byte_length_not_char_length():
    # A 2-byte UTF-8 char (é) → the length prefix counts BYTES, not codepoints.
    payload = "é".encode("utf-8")  # 2 bytes
    pae = dsse.dsse_pae("t", payload)
    assert pae == b"DSSEv1 1 t 2 " + payload


def test_kat_reproduces_frozen_signature():
    """Determinism anchor: fixed seed + payload → the frozen SIG/pubkey/keyid.
    A drift here means the scheme diverged from dokan and cross-verify breaks."""
    key = dsse.key_from_seed_b64(YORU_DOKAN_KAT["seed_b64"])
    assert key.public_b64 == YORU_DOKAN_KAT["pub_b64"]
    assert key.keyid == YORU_DOKAN_KAT["keyid"]
    sig = key.sign(dsse.dsse_pae(dsse.DSSE_PAYLOAD_TYPE, YORU_DOKAN_KAT["payload"]))
    assert sig == YORU_DOKAN_KAT["sig"]


def test_ed_verify_accepts_kat():
    assert dsse.ed_verify(
        YORU_DOKAN_KAT["pub_b64"],
        dsse.DSSE_PAYLOAD_TYPE,
        YORU_DOKAN_KAT["payload"],
        YORU_DOKAN_KAT["sig"],
    )


def test_sign_verify_round_trip():
    key = dsse.key_from_seed_b64(dsse.generate_seed_b64())
    msg = dsse.dsse_pae(dsse.DSSE_PAYLOAD_TYPE, b'{"x":1}')
    sig = key.sign(msg)
    assert dsse.ed_verify(key.public_b64, dsse.DSSE_PAYLOAD_TYPE, b'{"x":1}', sig)


def test_tamper_is_rejected():
    key = dsse.key_from_seed_b64(YORU_DOKAN_KAT["seed_b64"])
    sig = key.sign(dsse.dsse_pae(dsse.DSSE_PAYLOAD_TYPE, b'{"x":1}'))
    # Flip the payload → same signature must NOT verify.
    assert not dsse.ed_verify(
        key.public_b64, dsse.DSSE_PAYLOAD_TYPE, b'{"x":2}', sig
    )
    # Corrupt the signature → reject.
    bad = ("00" + sig[2:]) if sig[:2] != "00" else ("11" + sig[2:])
    assert not dsse.ed_verify(
        key.public_b64, dsse.DSSE_PAYLOAD_TYPE, b'{"x":1}', bad
    )


def test_ed_verify_malformed_never_raises():
    assert dsse.ed_verify("not-b64!!", "t", b"x", "zz") is False
    assert dsse.ed_verify("", "t", b"x", "") is False


def test_build_envelope_shape_matches_dokan():
    key = dsse.key_from_seed_b64(YORU_DOKAN_KAT["seed_b64"])
    statement = {"_type": "https://in-toto.io/Statement/v1", "predicate": {"n": 1}}
    env = dsse.build_envelope(statement, key)

    assert env["dsse"]["payloadType"] == dsse.DSSE_PAYLOAD_TYPE
    assert env["ed25519"]["public_key"] == key.public_b64
    assert env["ed25519"]["keyid"] == key.keyid
    sig_entry = env["dsse"]["signatures"][0]
    assert sig_entry["keyid"] == key.keyid
    # The embedded base64 payload decodes to the exact bytes that were signed.
    payload_bytes = base64.standard_b64decode(env["dsse"]["payload"])
    assert json.loads(payload_bytes) == statement
    assert dsse.ed_verify(
        key.public_b64, dsse.DSSE_PAYLOAD_TYPE, payload_bytes, sig_entry["sig"]
    )


def test_verify_envelope_round_trip_and_tamper():
    key = dsse.key_from_seed_b64(YORU_DOKAN_KAT["seed_b64"])
    env = dsse.build_envelope({"predicate": {"n": 1}}, key)
    assert dsse.verify_envelope(env) is True

    # Tamper the payload without re-signing → verify fails.
    forged = json.loads(json.dumps(env))
    forged["dsse"]["payload"] = base64.standard_b64encode(
        b'{"predicate":{"n":999}}'
    ).decode("ascii")
    assert dsse.verify_envelope(forged) is False


def test_load_signing_key_from_env_seed(monkeypatch):
    monkeypatch.setenv("YORU_SIGNING_KEY", YORU_DOKAN_KAT["seed_b64"])
    key = dsse.load_signing_key()
    assert key is not None
    assert key.public_b64 == YORU_DOKAN_KAT["pub_b64"]


def test_load_signing_key_from_keyfile(monkeypatch, tmp_path):
    kf = tmp_path / "yoru.key"
    kf.write_text(YORU_DOKAN_KAT["seed_b64"] + "\n")
    monkeypatch.setenv("YORU_SIGNING_KEY", str(kf))
    key = dsse.load_signing_key()
    assert key is not None and key.keyid == YORU_DOKAN_KAT["keyid"]


def test_load_signing_key_unset_or_bad_returns_none(monkeypatch):
    monkeypatch.delenv("YORU_SIGNING_KEY", raising=False)
    assert dsse.load_signing_key() is None
    monkeypatch.setenv("YORU_SIGNING_KEY", "not-a-valid-seed")
    assert dsse.load_signing_key() is None
