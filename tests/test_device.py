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


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def test_pairing_locks_after_repeated_wrong_codes(devices: DeviceRegistry) -> None:
    code = devices.start_pairing("u1")
    for _ in range(devices.max_attempts):
        with pytest.raises(PairingError):
            devices.pair(_wrong(code), name="guess")
    with pytest.raises(PairingError):
        devices.pair(code, name="right but too late")


def test_a_new_pairing_code_does_not_reset_the_lockout(devices: DeviceRegistry) -> None:
    """Starting pairing must not hand a guesser a fresh budget (anyone who can start pairing could reset it)."""
    code = devices.start_pairing("victim")
    for _ in range(devices.max_attempts):
        with pytest.raises(PairingError):
            devices.pair(_wrong(code), name="guess")
    devices.start_pairing("someone-else")
    with pytest.raises(PairingError, match="too many"):
        devices.pair(code, name="right but locked")


def test_the_lockout_lifts_when_its_window_passes(devices: DeviceRegistry, clock: Clock) -> None:
    for _ in range(devices.max_attempts):
        with pytest.raises(PairingError):
            devices.pair("999999", name="guess")
    clock.advance(devices.attempt_window + 1)
    assert devices.pair(devices.start_pairing("u1"), name="later").user_id == "u1"


def test_right_codes_do_not_use_up_the_attempt_budget(devices: DeviceRegistry) -> None:
    for i in range(devices.max_attempts * 3):
        assert devices.pair(devices.start_pairing(f"u{i}"), name="ok").user_id == f"u{i}"


def test_concurrent_wrong_guesses_are_not_all_evaluated(devices: DeviceRegistry) -> None:
    """The budget is taken before the code is looked up, so a burst of parallel guesses can't outrun the lock."""
    import threading

    code = devices.start_pairing("victim")
    looked_up: list[str] = []
    real_get = devices.store.get

    def counting_get(kind: str, key: str) -> Any:
        if kind == "pairing":
            looked_up.append(key)
        return real_get(kind, key)

    devices.store.get = counting_get  # type: ignore[method-assign]
    barrier = threading.Barrier(40)

    def guess(i: int) -> None:
        barrier.wait()
        try:
            devices.pair(f"{(int(code) + 1 + i) % 10**6:06d}", name="burst")
        except PairingError:
            pass

    ts = [threading.Thread(target=guess, args=(i,)) for i in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(looked_up) <= devices.max_attempts


def test_issue_creates_a_device_without_a_code(devices: DeviceRegistry, store: Any) -> None:
    d = devices.issue("guest-1", name="Demo visitor")
    assert devices.get(d.device_id) == d and d.user_id == "guest-1"
    assert store.scan("pairing") == []


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


def test_one_sources_wrong_guesses_do_not_lock_out_the_owner(devices: DeviceRegistry) -> None:
    """Third review, finding 3: five guesses from anyone locked every pairing (and every Alexa+ link)."""
    code = devices.start_pairing("owner")
    for _ in range(devices.max_attempts):
        with pytest.raises(PairingError):
            devices.pair(_wrong(code), name="guess", source="203.0.113.9")
    with pytest.raises(PairingError, match="too many"):
        devices.pair(code, name="attacker's right guess", source="203.0.113.9")
    assert devices.pair(code, name="owner", source="198.51.100.7").user_id == "owner"


def test_the_global_ceiling_still_bounds_guessing_from_many_sources(devices: DeviceRegistry) -> None:
    code = devices.start_pairing("owner")
    for i in range(devices.global_max_attempts):
        with pytest.raises(PairingError):
            devices.pair(_wrong(code), name="guess", source=f"10.0.0.{i}")
    with pytest.raises(PairingError, match="too many"):
        devices.pair(code, name="fresh source", source="10.9.9.9")
