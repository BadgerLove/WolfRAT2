"""Phone notifications: message encryption (RFC 8291), signing (RFC 8292), sending."""
import asyncio
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from wolfrat import web_push
from wolfrat.web_push import VapidKeys, b64url, unb64url

# RFC 8291 Appendix A: the worked example, byte for byte.
RFC = {
    "plaintext": "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "body": ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLoc"
             "InmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLV"
             "WGNWQexSgSxsj_Qulcy4a-fN"),
}


def private(b64):
    return ec.derive_private_key(int.from_bytes(unb64url(b64), "big"), ec.SECP256R1())


def test_rfc_keys_are_what_the_rfc_says():
    assert VapidKeys.from_private(RFC["as_private"]).public_b64 == RFC["as_public"]
    assert VapidKeys.from_private(RFC["ua_private"]).public_b64 == RFC["ua_public"]


def test_encrypt_reproduces_the_rfc_example_exactly():
    body = web_push.encrypt(unb64url(RFC["plaintext"]), RFC["ua_public"], RFC["auth"],
                            salt=unb64url(RFC["salt"]), as_private=private(RFC["as_private"]))
    assert b64url(body) == RFC["body"]


def test_the_phone_can_read_what_was_sent():
    phone = ec.generate_private_key(ec.SECP256R1())
    phone_public = b64url(web_push._public_bytes(phone.public_key()))
    auth = b"0123456789abcdef"
    message = json.dumps({"title": "[UK] Mixed Maps LC", "body": "Kestrel joined"}).encode()
    body = web_push.encrypt(message, phone_public, b64url(auth))
    assert web_push.decrypt(body, phone, auth) == message
    assert web_push.decrypt(web_push.encrypt(message, phone_public, b64url(auth)), phone, auth) == message
    assert body != web_push.encrypt(message, phone_public, b64url(auth))    # fresh key + salt each time


def test_payload_size_limit():
    phone = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(ValueError):
        web_push.encrypt(b"x" * (web_push.MAX_PAYLOAD + 1),
                         b64url(web_push._public_bytes(phone.public_key())), b64url(b"0" * 16))


def test_vapid_header_is_a_valid_es256_token_for_that_push_service():
    keys = VapidKeys.generate()
    again = VapidKeys.from_private(keys.private_b64)
    assert again.public_b64 == keys.public_b64 and len(unb64url(keys.public_b64)) == 65
    header = keys.authorization("https://fcm.googleapis.com/fcm/send/abc:def", now=1_000_000)
    assert header.startswith("vapid t=") and header.endswith(f", k={keys.public_b64}")
    token = header[len("vapid t="):header.index(", k=")]
    head, claims, signature = token.split(".")
    assert json.loads(unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(unb64url(claims)) == {"aud": "https://fcm.googleapis.com",
                                            "exp": 1_000_000 + 12 * 3600,
                                            "sub": "https://fmj-squad.com/wolfra.html"}
    raw = unb64url(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64url(keys.public_b64))
    public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))   # raises if wrong


def subscription(endpoint="https://fcm.googleapis.com/fcm/send/abc"):
    phone = ec.generate_private_key(ec.SECP256R1())
    return {"endpoint": endpoint,
            "keys": {"p256dh": b64url(web_push._public_bytes(phone.public_key())),
                     "auth": b64url(b"fedcba9876543210")}}, phone


@pytest.mark.parametrize("endpoint, ok", [
    ("https://fcm.googleapis.com/fcm/send/x", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://web.push.apple.com/QO", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/x", False),
    ("https://evilgoogleapis.com/x", False),
    ("https://127.0.0.1/x", False),
    ("https://example.com/x", False),
])
def test_only_known_push_services(endpoint, ok):
    sub, _ = subscription(endpoint)
    assert (web_push.valid_subscription(sub) is None) is ok


def test_bad_keys_are_refused():
    sub, _ = subscription()
    assert web_push.valid_subscription({**sub, "keys": {"p256dh": "AAAA", "auth": sub["keys"]["auth"]}})
    assert web_push.valid_subscription({**sub, "keys": {"p256dh": sub["keys"]["p256dh"], "auth": "AAAA"}})
    assert web_push.valid_subscription("nope")
    assert web_push.valid_subscription({"endpoint": sub["endpoint"]})


class FakeResponse:
    def __init__(self, status, text=""):
        self.status, self._text = status, text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, status=201, text="", boom=None):
        self.status, self.text, self.boom, self.calls = status, text, boom, []

    def post(self, url, data=None, headers=None, timeout=None):
        if self.boom:
            raise self.boom
        self.calls.append((url, data, headers))
        return FakeResponse(self.status, self.text)


def test_send_posts_one_encrypted_message_the_phone_can_read():
    keys = VapidKeys.generate()
    sub, phone = subscription()
    session = FakeSession(201)
    result = asyncio.run(web_push.send(session, sub, {"title": "T", "body": "Kestrel joined"}, keys))
    assert result == web_push.PushResult(201, True, False, None)
    url, body, headers = session.calls[0]
    assert url == sub["endpoint"]
    assert headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == "3600"
    assert headers["Urgency"] == "high" and headers["Authorization"].startswith("vapid t=")
    sent = json.loads(web_push.decrypt(body, phone, unb64url(sub["keys"]["auth"])))
    assert sent == {"title": "T", "body": "Kestrel joined"}


def test_send_reports_gone_errors_and_never_raises():
    keys = VapidKeys.generate()
    sub, _ = subscription()
    gone = asyncio.run(web_push.send(FakeSession(410, "expired"), sub, {"a": 1}, keys))
    assert gone.gone and not gone.ok and gone.status == 410
    bad = asyncio.run(web_push.send(FakeSession(400, "bad"), sub, {"a": 1}, keys))
    assert not bad.ok and not bad.gone and "400" in bad.error
    down = asyncio.run(web_push.send(FakeSession(boom=OSError("no route")), sub, {"a": 1}, keys))
    assert down.status == 0 and not down.ok and "no route" in down.error
    refused = asyncio.run(web_push.send(FakeSession(), {"endpoint": "https://example.com/x"}, {}, keys))
    assert refused.status == 0 and "push service" in refused.error
