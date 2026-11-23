"""Phone notifications for the Web Admin page (Web Push).

WolfRAT sends each notification straight to the push service of the phone's
browser (Google's for Chrome, Mozilla's, Apple's, Microsoft's). The message is
encrypted for that one phone (RFC 8291, aes128gcm); the push service only
passes it on. There is no account or API key: WolfRAT makes its own key pair
once (VAPID, RFC 8292) and signs each request with it.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Push services a subscription may point at (the host or a subdomain of it).
PUSH_HOSTS = ("googleapis.com", "mozilla.com", "push.apple.com", "notify.windows.com")
# Who sends: the push services want a contact; this is the project page, no person.
CONTACT = "https://fmj-squad.com/wolfra.html"
RECORD_SIZE = 4096
MAX_PAYLOAD = RECORD_SIZE - 16 - 1 - 86     # tag, delimiter, header
JWT_LIFETIME = 12 * 3600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    text = str(text).strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(public_key) -> bytes:
    return public_key.public_bytes(serialization.Encoding.X962,
                                   serialization.PublicFormat.UncompressedPoint)


def _private_from_bytes(raw: bytes):
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


class VapidKeys:
    """WolfRAT's own P-256 key pair: the phone subscribes with the public half."""

    def __init__(self, private_key):
        self._private = private_key
        self.private_b64 = b64url(private_key.private_numbers().private_value.to_bytes(32, "big"))
        self.public_b64 = b64url(_public_bytes(private_key.public_key()))

    @staticmethod
    def generate() -> "VapidKeys":
        return VapidKeys(ec.generate_private_key(ec.SECP256R1()))

    @staticmethod
    def from_private(private_b64: str) -> "VapidKeys":
        raw = unb64url(private_b64)
        if len(raw) != 32:
            raise ValueError("a VAPID private key is 32 bytes")
        return VapidKeys(_private_from_bytes(raw))

    def authorization(self, endpoint: str, now: Optional[float] = None) -> str:
        """The Authorization header for one push service (RFC 8292)."""
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int((time.time() if now is None else now) + JWT_LIFETIME),
            "sub": CONTACT,
        }
        header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        body = b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = f"{header}.{body}".encode("ascii")
        r, s = decode_dss_signature(self._private.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={header}.{body}.{signature}, k={self.public_b64}"


class PushResult(NamedTuple):
    status: int            # HTTP status from the push service (0 = never sent)
    ok: bool
    gone: bool             # 404 / 410: the phone unsubscribed; drop the subscription
    error: Optional[str]


def valid_subscription(subscription) -> Optional[str]:
    """None when the subscription can be used, else the reason (for a 400 answer)."""
    if not isinstance(subscription, dict):
        return "subscription must be an object"
    endpoint = subscription.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return "subscription endpoint missing"
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return "subscription endpoint must be https"
    if not any(host == known or host.endswith("." + known) for known in PUSH_HOSTS):
        return "subscription endpoint is not a known push service"
    keys = subscription.get("keys")
    if not isinstance(keys, dict):
        return "subscription keys missing"
    try:
        p256dh = unb64url(keys.get("p256dh", ""))
        auth = unb64url(keys.get("auth", ""))
    except (ValueError, TypeError):
        return "subscription keys are not base64url"
    if len(p256dh) != 65 or p256dh[0] != 4:
        return "subscription key p256dh must be an uncompressed P-256 point"
    if len(auth) != 16:
        return "subscription key auth must be 16 bytes"
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    except ValueError:
        return "subscription key p256dh is not on the curve"
    return None


def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, *,
            salt: Optional[bytes] = None, as_private=None) -> bytes:
    """One aes128gcm push message body for one phone (RFC 8291 / RFC 8188).

    ``salt`` and ``as_private`` (the one-time sender key) are only passed by
    tests, to reproduce the RFC's example; normally both are fresh each time.
    """
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"push payload too long ({len(payload)} > {MAX_PAYLOAD} bytes)")
    ua_public = unb64url(p256dh_b64)
    auth_secret = unb64url(auth_b64)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_key = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_key.public_key())
    salt = os.urandom(16) if salt is None else salt
    ecdh_secret = as_key.exchange(ec.ECDH(), ua_key)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, key_info).derive(ecdh_secret)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def decrypt(body: bytes, ua_private, auth_secret: bytes) -> bytes:
    """The phone's side of :func:`encrypt` (tests only)."""
    salt, (record_size, id_len) = body[:16], struct.unpack("!IB", body[16:21])
    as_public = body[21:21 + id_len]
    ciphertext = body[21 + id_len:]
    ua_public = _public_bytes(ua_private.public_key())
    as_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh_secret = ua_private.exchange(ec.ECDH(), as_key)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, key_info).derive(ecdh_secret)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    return padded.rstrip(b"\x00")[:-1]          # drop padding and the 0x02 delimiter


async def send(session, subscription: dict, payload, keys: VapidKeys, *,
               ttl: int = 3600, urgency: str = "high") -> PushResult:
    """Send one notification. ``payload``: a dict (sent as JSON) or bytes."""
    reason = valid_subscription(subscription)
    if reason:
        return PushResult(0, False, False, reason)
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    else:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    endpoint = subscription["endpoint"]
    try:
        body = encrypt(data, subscription["keys"]["p256dh"], subscription["keys"]["auth"])
    except ValueError as error:
        return PushResult(0, False, False, str(error))
    headers = {
        "TTL": str(int(ttl)),
        "Urgency": urgency,
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": keys.authorization(endpoint),
    }
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=20)
        async with session.post(endpoint, data=body, headers=headers, timeout=timeout) as resp:
            status = resp.status
            text = (await resp.text())[:200] if status >= 300 else ""
    except Exception as error:                         # network trouble: report, never raise
        return PushResult(0, False, False, f"{type(error).__name__}: {error}")
    if 200 <= status < 300:
        return PushResult(status, True, False, None)
    return PushResult(status, False, status in (404, 410), f"HTTP {status} {text}".strip())
