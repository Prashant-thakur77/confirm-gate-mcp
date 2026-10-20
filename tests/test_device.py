"""The device channel: the only door a nonce leaves through, and the only door evidence enters by."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from confirm_gate import AuthError, DeviceRegistry, Gate, PairingError, device_routes, sign_request

from .conftest import FAN_OPTIONS, Clock


def test_pairing_code_is_one_time(devices: DeviceRegistry) -> None:
    code = devices.start_pairing("u1")
    assert len(code) == 6 and code.isdigit()
    d = devices.pair(code, name="tablet")
    assert d.user_id == "u1" and len(d.key) >= 32
    with pytest.raises(PairingError):
        devices.pair(code, name="again")


def test_pairing_code_expires(devices: DeviceRegistry, clock: Clock) -> None:
    code = devices.start_pairing("u1")
    clock.advance(devices.pairing_ttl + 1)
    with pytest.raises(PairingError):
        devices.pair(code, name="late")


def test_pairing_locks_after_repeated_wrong_codes(devices: DeviceRegistry) -> None:
    code = devices.start_pairing("u1")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(devices.max_attempts):
        with pytest.raises(PairingError):
            devices.pair(wrong, name="guess")
    with pytest.raises(PairingError):
        devices.pair(code, name="right but too late")


def _headers(device: Any, method: str, path: str, body: bytes, ts: float) -> dict[str, str]:
    return {
        "x-device-id": device.device_id,
        "x-timestamp": str(int(ts)),
        "x-signature": sign_request(device.key, method, path, int(ts), body),
    }


def test_a_signed_request_authenticates(devices: DeviceRegistry, device: Any, clock: Clock) -> None:
    h = _headers(device, "GET", "/device/pending", b"", clock())
    assert devices.authenticate(h, "GET", "/device/pending", b"").device_id == device.device_id


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h, d: {**h, "x-signature": "0" * 64},
        lambda h, d: {**h, "x-device-id": "d_nope"},
        lambda h, d: {k: v for k, v in h.items() if k != "x-signature"},
        lambda h, d: {**h, "x-timestamp": str(int(h["x-timestamp"]) - 120)},
    ],
)
def test_bad_signatures_are_rejected(devices: DeviceRegistry, device: Any, clock: Clock, mutate: Any) -> None:
    h = mutate(_headers(device, "POST", "/device/evidence", b"{}", clock()), device)
    with pytest.raises(AuthError):
        devices.authenticate(h, "POST", "/device/evidence", b"{}")


def test_a_changed_body_breaks_the_signature(devices: DeviceRegistry, device: Any, clock: Clock) -> None:
    h = _headers(device, "POST", "/device/evidence", b'{"option": 1}', clock())
    with pytest.raises(AuthError):
        devices.authenticate(h, "POST", "/device/evidence", b'{"option": 3}')


def test_a_signature_is_accepted_once(devices: DeviceRegistry, device: Any, clock: Clock) -> None:
    h = _headers(device, "GET", "/device/pending", b"", clock())
    devices.authenticate(h, "GET", "/device/pending", b"")
    with pytest.raises(AuthError):
        devices.authenticate(h, "GET", "/device/pending", b"")


def test_a_revoked_device_is_rejected(devices: DeviceRegistry, device: Any, clock: Clock) -> None:
    devices.revoke(device.device_id)
    h = _headers(device, "GET", "/device/pending", b"", clock())
    with pytest.raises(AuthError):
        devices.authenticate(h, "GET", "/device/pending", b"")


@pytest.fixture()
def client(gate: Gate, devices: DeviceRegistry) -> TestClient:
    app = Starlette(routes=device_routes(gate, devices, transcribe=lambda audio: audio.decode()))
    return TestClient(app)


def _signed(client: TestClient, device: Any, clock: Clock, method: str, path: str, payload: Any = None):
    body = b"" if payload is None else json.dumps(payload).encode()
    h = _headers(device, method, path, body, clock())
    if body:
        h["content-type"] = "application/json"
    return client.request(method, path, content=body or None, headers=h)


def test_routes_pair_list_and_take_tap_evidence(client: TestClient, gate: Gate, devices: DeviceRegistry,
                                                clock: Clock, house: Any) -> None:
    code = devices.start_pairing("u1")
    r = client.post("/device/pair", json={"code": code, "name": "browser"})
    assert r.status_code == 200
    from confirm_gate.evidence import Device

    d = Device(**{k: r.json()[k] for k in ("device_id", "user_id", "key", "name")})
    ps = gate.propose("u1", FAN_OPTIONS)
    assert client.get("/device/pending").status_code == 401
    listed = _signed(client, d, clock, "GET", "/device/pending").json()["pending"]
    assert listed[0]["pending_id"] == ps.pending_id and listed[0]["nonce"]
    ev = _signed(client, d, clock, "POST", "/device/evidence",
                 {"pending_id": ps.pending_id, "nonce": listed[0]["nonce"], "kind": "tap", "option": 1}).json()
    assert ev["decision"]["option"] == 1
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "done" and house.devices["fan"] == "on"


def test_routes_take_voice_evidence(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    audio = base64.b64encode(b"the third one").decode()
    r = _signed(client, device, clock, "POST", "/device/evidence",
                {"pending_id": ps.pending_id, "nonce": nonce, "kind": "voice", "audio_b64": audio})
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == {"kind": "choose", "option": 3, "heard": "the 3 one"} or r.json()["decision"]["option"] == 3


def test_routes_refuse_a_wrong_nonce(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    r = _signed(client, device, clock, "POST", "/device/evidence",
                {"pending_id": ps.pending_id, "nonce": "nope", "kind": "tap", "option": 1})
    assert r.status_code == 409
