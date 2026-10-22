"""More time (WCAG 2.2.1 Timing Adjustable): the user may keep a waiting card set open, from their own device only.

Extending never answers anything: it moves one waiting set's deadline, and only the house's state proves a write.
"""

from __future__ import annotations

import json
import secrets
from importlib import resources
from typing import Any

import pytest
from confirm_gate import (
    DeviceRegistry,
    EvidenceRejected,
    Gate,
    GatedServer,
    MemoryStore,
    NotWaiting,
    device_routes,
    sign_request,
    wrap_upstream,
)
from mcp import Client
from mcp.server import MCPServer
from starlette.applications import Starlette
from starlette.testclient import TestClient

from .conftest import FAN_OPTIONS, Clock, House


def test_more_time_restarts_the_sets_own_clock(gate: Gate, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(100)
    out = gate.extend(ps.pending_id, user_id="u1")
    assert out["state"] == "waiting_for_user"
    assert out["expires_in_s"] == 120
    assert out["pending_id"] == ps.pending_id
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 120


def test_a_final_yes_gets_its_own_shorter_time(gate: Gate, clock: Clock) -> None:
    ps = gate.propose("u1", [("cart_order", {"item": "milk", "qty": 1})])
    assert ps.stage == "final"
    clock.advance(50)
    assert gate.extend(ps.pending_id, user_id="u1")["expires_in_s"] == 60


def test_the_user_can_then_answer_after_the_first_deadline(gate: Gate, house: House, device: Any,
                                                          clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(110)
    gate.extend(ps.pending_id, user_id="u1")
    clock.advance(100)  # 210 s after it opened: past the first 120 s
    [open_set] = gate.device_pending(device)
    ev = gate.submit_tap(device, ps.pending_id, open_set["nonce"], option=1)
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "done", out
    assert house.devices["fan"] == "on"


def test_more_time_answers_nothing(gate: Gate, house: House, clock: Clock) -> None:
    before = house.snapshot()
    ps = gate.propose("u1", FAN_OPTIONS)
    for _ in range(3):
        gate.extend(ps.pending_id, user_id="u1")
    assert house.snapshot() == before
    assert gate.pending_status(ps.pending_id)["state"] == "waiting_for_user"
    out = gate.confirm(ps.pending_id, "")
    assert out["status"] == "refused"
    assert house.snapshot() == before


def test_the_reply_never_contains_the_nonce(gate: Gate) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    out = gate.extend(ps.pending_id, user_id="u1")
    assert "nonce" not in out
    assert ps.nonce not in json.dumps(out)


def test_more_time_never_shortens_a_set(gate: Gate, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    assert gate.extend(ps.pending_id, user_id="u1", seconds=10)["expires_in_s"] == 120
    assert gate.extend(ps.pending_id, user_id="u1", seconds=300)["expires_in_s"] == 300


@pytest.mark.parametrize("seconds", [0, -5, 601, 10**9, float("nan"), float("inf"), True, "60"])
def test_a_strange_amount_of_time_is_refused(gate: Gate, seconds: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    with pytest.raises(ValueError):
        gate.extend(ps.pending_id, user_id="u1", seconds=seconds)
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 120


@pytest.mark.parametrize("pid", ["p_nope", "", None, "../p"])
def test_an_unknown_set_is_unknown(gate: Gate, pid: Any) -> None:
    with pytest.raises(KeyError):
        gate.extend(pid, user_id="u1")


def test_another_users_set_looks_unknown_and_keeps_its_time(gate: Gate, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(100)
    with pytest.raises(KeyError):
        gate.extend(ps.pending_id, user_id="u2")
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 20


def test_an_answered_set_gets_no_more_time(gate: Gate, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    gate.confirm(ps.pending_id, gate.submit_tap(device, ps.pending_id, nonce, option=1)["evidence_ref"])
    with pytest.raises(NotWaiting):
        gate.extend(ps.pending_id, user_id="u1")
    assert gate.pending_status(ps.pending_id)["state"] == "confirmed"


def test_a_cancelled_or_withdrawn_set_gets_no_more_time(gate: Gate, device: Any) -> None:
    a = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    gate.confirm(a.pending_id, gate.submit_tap(device, a.pending_id, nonce, answer="no")["evidence_ref"])
    b = gate.propose("u1", FAN_OPTIONS)
    gate.withdraw_open("u1")
    for ps in (a, b):
        with pytest.raises(NotWaiting):
            gate.extend(ps.pending_id, user_id="u1")


def test_an_expired_set_stays_expired(gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    clock.advance(121)
    with pytest.raises(NotWaiting):
        gate.extend(ps.pending_id, user_id="u1")
    assert gate.pending_status(ps.pending_id)["state"] == "expired"
    assert gate.device_pending(device) == []
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, ps.pending_id, nonce, option=1)


def test_more_time_is_capped_per_set(gate: Gate, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    for i in range(Gate.max_extensions):
        out = gate.extend(ps.pending_id, user_id="u1")
        assert out["extensions_left"] == Gate.max_extensions - i - 1
    with pytest.raises(NotWaiting):
        gate.extend(ps.pending_id, user_id="u1")
    clock.advance(121)
    assert gate.pending_status(ps.pending_id)["state"] == "expired"


def test_more_time_is_an_audit_event(store: Any, house: House, clock: Clock) -> None:
    from .conftest import make_registry

    events: list[dict[str, Any]] = []
    g = Gate(store, make_registry(house), clock=clock, on_event=events.append)
    ps = g.propose("u1", FAN_OPTIONS)
    clock.advance(30)
    g.extend(ps.pending_id, user_id="u1")
    [ev] = [e for e in events if e["type"] == "extended"]
    assert ev["pending_id"] == ps.pending_id and ev["user_id"] == "u1"
    assert ev["expires_in_s"] == 120 and ev["extensions"] == 1 and ev["at"] == clock.now
    assert ps.nonce not in json.dumps(events)


# -- never an agent's power -------------------------------------------------------------------------------------------

async def test_more_time_is_not_an_mcp_tool_of_a_gated_server() -> None:
    mcp = MCPServer("gated-test")
    gs = GatedServer(mcp, store=MemoryStore(), clock=Clock(), user=lambda: "u1")

    @gs.write(risk="low")
    def home_set(device: str, state: str) -> dict[str, Any]:
        """Switch a device."""
        return {"ok": True}

    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        p = json.loads((await c.call_tool("home_set", {"device": "fan", "state": "on"})).content[0].text)
        for name in ("extend", "more_time"):
            out = await c.call_tool(name, {"pending_id": p["pending_id"]})
            assert out.is_error
    assert names == {"home_set", "confirm", "pending_status"}
    assert not any("extend" in n or "time" in n for n in names)
    assert gs.gate.pending_status(p["pending_id"])["expires_in_s"] == 120


async def test_more_time_is_not_a_tool_of_the_proxy() -> None:
    upstream = MCPServer("notes")

    @upstream.tool()
    def write_note(name: str, text: str) -> str:
        """Write a note."""
        return "written"

    clock = Clock()
    proxy, gate = wrap_upstream(upstream, store=MemoryStore(), clock=clock, user=lambda: "u1")
    async with Client(proxy) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        p = json.loads((await c.call_tool("write_note", {"name": "a", "text": "hi"})).content[0].text)
        for name in ("extend", "more_time"):
            out = await c.call_tool(name, {"pending_id": p["pending_id"]})
            assert out.is_error
    assert names == {"write_note", "confirm", "pending_status"}
    assert gate.pending_status(p["pending_id"])["expires_in_s"] == 120


# -- the device route: the user's own signed device, never the agent ------------------------------------------------

@pytest.fixture()
def client(gate: Gate, devices: DeviceRegistry) -> TestClient:
    return TestClient(Starlette(routes=device_routes(gate, devices, transcribe=lambda audio: audio.decode())))


def _more_time(client: TestClient, device: Any, clock: Clock, payload: Any, *, sign: bool = True) -> Any:
    body = json.dumps(payload).encode()
    path = f"/device/more-time?r={secrets.token_hex(6)}"  # as the page does: each signature is accepted once
    h = {"content-type": "application/json"}
    if sign:
        ts = int(clock())
        h |= {"x-device-id": device.device_id, "x-timestamp": str(ts),
              "x-signature": sign_request(device.key, "POST", path, ts, body)}
    return client.post(path, content=body, headers=h)


def test_the_route_gives_the_owners_device_more_time(client: TestClient, gate: Gate, device: Any,
                                                     clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(100)
    r = _more_time(client, device, clock, {"pending_id": ps.pending_id})
    assert r.status_code == 200, r.text
    assert r.json()["expires_in_s"] == 120
    assert r.json()["extensions_left"] == Gate.max_extensions - 1
    assert r.json()["state"] == "waiting_for_user"
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 120


def test_the_routes_reply_never_carries_the_nonce(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    r = _more_time(client, device, clock, {"pending_id": ps.pending_id})
    assert r.status_code == 200
    assert "nonce" not in r.json()
    assert ps.nonce not in r.text


def test_the_route_answers_nothing(client: TestClient, gate: Gate, house: House, device: Any, clock: Clock) -> None:
    before = house.snapshot()
    ps = gate.propose("u1", FAN_OPTIONS)
    for _ in range(3):
        assert _more_time(client, device, clock, {"pending_id": ps.pending_id}).status_code == 200
    assert house.snapshot() == before
    assert gate.pending_status(ps.pending_id)["state"] == "waiting_for_user"


def test_another_users_device_gets_404_and_the_set_keeps_its_time(client: TestClient, gate: Gate,
                                                                  devices: DeviceRegistry, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(100)
    stranger = devices.pair(devices.start_pairing("u2"), name="stranger")
    r = _more_time(client, stranger, clock, {"pending_id": ps.pending_id})
    assert r.status_code == 404
    assert ps.nonce not in r.text
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 20


@pytest.mark.parametrize("payload", [{"pending_id": "p_nope"}, {"pending_id": ""}, {}, {"pending_id": 7},
                                     {"pending_id": ["p_x"]}])
def test_an_unknown_set_is_404(client: TestClient, device: Any, clock: Clock, payload: Any) -> None:
    assert _more_time(client, device, clock, payload).status_code == 404


def test_an_answered_set_is_409(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    gate.confirm(ps.pending_id, gate.submit_tap(device, ps.pending_id, nonce, option=1)["evidence_ref"])
    r = _more_time(client, device, clock, {"pending_id": ps.pending_id})
    assert r.status_code == 409
    assert gate.pending_status(ps.pending_id)["state"] == "confirmed"


def test_an_expired_or_withdrawn_set_is_409(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    a = gate.propose("u1", FAN_OPTIONS)
    clock.advance(121)
    b = gate.propose("u1", FAN_OPTIONS)
    gate.withdraw_open("u1")
    for ps in (a, b):
        assert _more_time(client, device, clock, {"pending_id": ps.pending_id}).status_code == 409


def test_a_set_out_of_more_time_is_409(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    for _ in range(Gate.max_extensions):
        r = _more_time(client, device, clock, {"pending_id": ps.pending_id})
    assert r.json()["extensions_left"] == 0
    assert _more_time(client, device, clock, {"pending_id": ps.pending_id}).status_code == 409


def test_an_unsigned_request_is_refused(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(100)
    r = _more_time(client, device, clock, {"pending_id": ps.pending_id}, sign=False)
    assert r.status_code == 401
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 20


def test_a_revoked_device_is_refused(client: TestClient, gate: Gate, devices: DeviceRegistry, device: Any,
                                     clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    clock.advance(100)
    devices.revoke(device.device_id)
    assert _more_time(client, device, clock, {"pending_id": ps.pending_id}).status_code == 401
    assert gate.pending_status(ps.pending_id)["expires_in_s"] == 20


def test_the_device_cannot_choose_its_own_amount(client: TestClient, gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    r = _more_time(client, device, clock, {"pending_id": ps.pending_id, "seconds": 600})
    assert r.status_code == 200
    assert r.json()["expires_in_s"] == 120


# -- the built-in approval page ---------------------------------------------------------------------------------------

def _page() -> str:
    return resources.files("confirm_gate").joinpath("approve.html").read_text()


def test_the_approval_page_has_a_more_time_button_that_calls_the_route() -> None:
    page = _page()
    assert "More time" in page
    assert 'signed("POST", "/device/more-time", { pending_id: set.pending_id })' in page
    assert "expires_in_s" in page and "extensions_left" in page


def test_the_approval_page_warns_politely_at_30_seconds() -> None:
    page = _page()
    assert 'aria-live="polite"' in page
    # at 30 s this reads "30 seconds left. Choose More time to keep these cards."
    assert "left <= 30" in page
    assert "tell(btn && !btn.hidden ? `${left} seconds left. Choose More time to keep these cards.`" in page


def test_the_approval_page_never_sends_the_nonce_for_more_time() -> None:
    page = _page()
    call = page[page.index('"/device/more-time"'):]
    assert "nonce" not in call[:call.index(")")]
