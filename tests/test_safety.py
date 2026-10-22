"""The gate's one promise: nothing changes unless the user's own evidence releases it.

Every test asserts on the house's state, not just on the returned status.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from confirm_gate import EvidenceRejected, Gate
from confirm_gate.receipt import verify_fingerprint

from .conftest import FAN_OPTIONS, Clock, House


def _voice(text: str):
    """A transcriber stand-in: the audio bytes *are* the words, so tests stay offline."""
    return lambda audio: text


def test_proposing_changes_nothing(gate: Gate, house: House) -> None:
    before = house.snapshot()
    ps = gate.propose("u1", FAN_OPTIONS)
    assert ps.pending_id.startswith("p_")
    assert house.snapshot() == before


def test_public_view_never_contains_the_nonce(gate: Gate) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    view = gate.public_view(ps)
    assert ps.nonce not in json.dumps(view)
    assert "nonce" not in view


def test_tap_confirms_exactly_the_chosen_option(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=2)
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "done", out
    assert house.devices == {"fan": "off", "lights": "on"}
    assert house.calls == 1


def test_voice_confirms_the_spoken_choice(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_voice(device, ps.pending_id, nonce, b"audio-two", transcribe=_voice("number two"))
    assert ev["decision"]["kind"] == "choose" and ev["decision"]["option"] == 2
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "done"
    assert house.devices["lights"] == "on"
    assert out["receipt"]["evidence"]["transcript"] == "number two"
    assert out["receipt"]["evidence"]["channel"] == "voice"


@pytest.mark.parametrize("ref", [None, "", "ev_does_not_exist", "../../etc/passwd", "ev_" + "a" * 40])
def test_confirm_without_real_evidence_is_refused(gate: Gate, house: House, ref: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    before = house.snapshot()
    out = gate.confirm(ps.pending_id, ref)
    assert out["status"] == "refused"
    assert house.snapshot() == before


def test_evidence_for_another_pending_set_is_refused(gate: Gate, house: House, device: Any) -> None:
    a = gate.propose("u1", FAN_OPTIONS)
    b = gate.propose("u1", FAN_OPTIONS)
    nonce_a = next(p["nonce"] for p in gate.device_pending(device) if p["pending_id"] == a.pending_id)
    ev = gate.submit_tap(device, a.pending_id, nonce_a, option=1)
    before = house.snapshot()
    out = gate.confirm(b.pending_id, ev["evidence_ref"])
    assert out["status"] == "refused"
    assert house.snapshot() == before


def test_a_wrong_nonce_creates_no_evidence(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, ps.pending_id, "guessed-nonce", option=1)


def test_a_nonce_from_another_set_creates_no_evidence(gate: Gate, device: Any) -> None:
    a = gate.propose("u1", FAN_OPTIONS)
    b = gate.propose("u1", FAN_OPTIONS)
    nonce_a = next(p["nonce"] for p in gate.device_pending(device) if p["pending_id"] == a.pending_id)
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, b.pending_id, nonce_a, option=1)


def test_another_users_device_cannot_answer(gate: Gate, devices: Any, house: House) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    intruder = devices.pair(devices.start_pairing("u2"), name="someone else")
    assert gate.device_pending(intruder) == []
    nonce = ps.nonce  # even if it leaked
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(intruder, ps.pending_id, nonce, option=1)


def test_an_unknown_device_cannot_answer(gate: Gate, device: Any) -> None:
    from dataclasses import replace

    ps = gate.propose("u1", FAN_OPTIONS)
    forged = replace(device, device_id="d_forged")
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(forged, ps.pending_id, ps.nonce, option=1)


def test_an_expired_pending_set_takes_no_evidence(gate: Gate, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    clock.advance(gate.pending_ttl + 1)
    assert gate.device_pending(device) == []
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, ps.pending_id, nonce, option=1)


def test_evidence_does_not_outlive_its_pending_set(gate: Gate, house: House, device: Any, clock: Clock) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1)
    clock.advance(gate.pending_ttl + 1)
    before = house.snapshot()
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "refused" and "expired" in out["reason"]
    assert house.snapshot() == before


def test_replaying_used_evidence_is_refused(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1)
    assert gate.confirm(ps.pending_id, ev["evidence_ref"])["status"] == "done"
    house.devices["fan"] = "off"
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "refused"
    assert house.devices["fan"] == "off" and house.calls == 1


def test_a_consumed_set_takes_no_more_evidence(gate: Gate, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1)
    gate.confirm(ps.pending_id, ev["evidence_ref"])
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, ps.pending_id, nonce, option=2)


def test_the_same_recording_cannot_confirm_twice(gate: Gate, house: House, device: Any) -> None:
    a = gate.propose("u1", FAN_OPTIONS)
    nonce_a = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_voice(device, a.pending_id, nonce_a, b"same-bytes", transcribe=_voice("one"))
    assert gate.confirm(a.pending_id, ev["evidence_ref"])["status"] == "done"
    b = gate.propose("u1", FAN_OPTIONS)
    nonce_b = next(p["nonce"] for p in gate.device_pending(device) if p["pending_id"] == b.pending_id)
    with pytest.raises(EvidenceRejected, match="already been used"):
        gate.submit_voice(device, b.pending_id, nonce_b, b"same-bytes", transcribe=_voice("one"))
    assert house.calls == 1


@pytest.mark.parametrize("said", ["no", "cancel", "nahin", "none of these"])
def test_no_cancels_and_changes_nothing(gate: Gate, house: House, device: Any, said: str) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_voice(device, ps.pending_id, nonce, said.encode(), transcribe=_voice(said))
    before = house.snapshot()
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "cancelled"
    assert house.snapshot() == before
    assert gate.device_pending(device) == []


@pytest.mark.parametrize("said", ["um", "yes", "two three", "not two", "four", "turn on the fan"])
def test_unclear_words_are_refused_and_the_user_can_try_again(
    gate: Gate, house: House, device: Any, said: str
) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_voice(device, ps.pending_id, nonce, said.encode(), transcribe=_voice(said))
    before = house.snapshot()
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "refused"
    assert house.snapshot() == before
    retry = gate.submit_tap(device, ps.pending_id, nonce, option=3)
    assert gate.confirm(ps.pending_id, retry["evidence_ref"])["status"] == "done"
    assert house.calls == 1


@pytest.mark.parametrize("option", [0, 4, -1, 99])
def test_a_tap_outside_the_cards_is_rejected(gate: Gate, device: Any, option: int) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, ps.pending_id, nonce, option=option)


def test_options_changed_after_evidence_are_refused(gate: Gate, store: Any, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1)
    doc = store.get("pending", ps.pending_id)
    doc["options"][0]["args"] = {"device": "lights", "state": "on"}
    store.put("pending", ps.pending_id, doc)
    before = house.snapshot()
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "refused" and "changed" in out["reason"]
    assert house.snapshot() == before


def test_two_confirms_racing_on_one_nonce_execute_once(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    refs = [gate.submit_tap(device, ps.pending_id, nonce, option=1)["evidence_ref"] for _ in range(2)]
    results: list[dict[str, Any]] = []
    barrier = threading.Barrier(8)

    def go(i: int) -> None:
        barrier.wait()
        results.append(gate.confirm(ps.pending_id, refs[i % 2]))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r["status"] == "done" for r in results) == 1
    assert house.calls == 1


def test_a_high_risk_choice_needs_a_second_yes(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", [("door_lock", {"locked": False}), ("door_lock", {"locked": True})])
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_voice(device, ps.pending_id, nonce, b"one", transcribe=_voice("one"))
    first = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert first["status"] == "needs_final_yes"
    assert house.door == "locked"
    final_id = first["pending_id"]
    assert final_id != ps.pending_id
    pending = gate.device_pending(device)
    assert [p["pending_id"] for p in pending] == [final_id]
    final_nonce = pending[0]["nonce"]
    assert final_nonce != nonce
    # the first evidence cannot release the final stage
    assert gate.confirm(final_id, ev["evidence_ref"])["status"] == "refused"
    # a number is not a yes
    num = gate.submit_voice(device, final_id, final_nonce, b"one again", transcribe=_voice("one"))
    assert gate.confirm(final_id, num["evidence_ref"])["status"] == "refused"
    assert house.door == "locked"
    yes = gate.submit_voice(device, final_id, final_nonce, b"yes", transcribe=_voice("yes"))
    done = gate.confirm(final_id, yes["evidence_ref"])
    assert done["status"] == "done"
    assert house.door == "unlocked"


def test_a_single_high_risk_proposal_goes_straight_to_yes_or_no(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", [("cart_order", {"item": "USB cable", "qty": 10})], origin="assistant")
    view = gate.device_pending(device)[0]
    assert view["stage"] == "final" and view["origin"] == "assistant"
    ev = gate.submit_voice(device, ps.pending_id, view["nonce"], b"no", transcribe=_voice("no"))
    assert gate.confirm(ps.pending_id, ev["evidence_ref"])["status"] == "cancelled"
    assert house.orders == []


def test_reads_cannot_be_proposed_as_writes(gate: Gate) -> None:
    with pytest.raises(ValueError):
        gate.propose("u1", [("home_status", {})])


def test_unknown_actions_cannot_be_proposed(gate: Gate) -> None:
    with pytest.raises(ValueError):
        gate.propose("u1", [("rm_rf", {})])


def test_a_failed_action_is_not_retried_with_the_same_consent(store: Any, clock: Clock, device: Any) -> None:
    from confirm_gate import ActionRegistry

    reg = ActionRegistry()
    calls = []

    @reg.write("flaky", risk="low", preview=lambda a: "Do the flaky thing")
    def flaky() -> dict[str, Any]:
        calls.append(1)
        raise RuntimeError("device offline")

    g = Gate(store, reg, clock=clock)
    ps = g.propose("u1", [("flaky", {})])
    nonce = g.device_pending(device)[0]["nonce"]
    ev = g.submit_tap(device, ps.pending_id, nonce, option=1)
    out = g.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "failed" and "device offline" in out["error"]
    assert g.confirm(ps.pending_id, ev["evidence_ref"])["status"] == "refused"
    assert len(calls) == 1


def test_the_receipt_says_who_what_when_and_how_and_is_tamper_evident(gate: Gate, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS, context={"heard": "van on"})
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1)
    receipt = gate.confirm(ps.pending_id, ev["evidence_ref"])["receipt"]
    assert receipt["who"]["user_id"] == "u1"
    assert receipt["who"]["device_id"] == device.device_id
    assert receipt["what"]["action"] == "home_set"
    assert receipt["what"]["args"] == {"device": "fan", "state": "on"}
    assert receipt["how"]["channel"] == "tap"
    assert receipt["when"]["confirmed_at"] >= receipt["when"]["proposed_at"]
    assert nonce not in json.dumps(receipt)
    assert all(c["ok"] for c in receipt["checks"])
    assert verify_fingerprint(receipt)
    receipt["what"]["args"]["state"] = "off"
    assert not verify_fingerprint(receipt)
    assert gate.receipts("u1")[0]["receipt_id"] == receipt["receipt_id"]


def test_device_view_shows_only_the_devices_own_open_set(gate: Gate, devices: Any, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    assert gate.device_view(device, ps.pending_id)["nonce"] == ps.nonce
    other = devices.pair(devices.start_pairing("u2"), name="other")
    assert gate.device_view(other, ps.pending_id) is None
    assert gate.device_view(device, "p_nope") is None


def test_a_paired_voice_device_can_send_its_own_transcript(gate: Gate, house: House, voice_device: Any) -> None:
    """A voice device (e.g. a smart speaker skill) recognises speech itself and sends the words, signed."""
    device = voice_device
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_transcript(device, ps.pending_id, nonce, "the second one")
    assert ev["decision"]["kind"] == "choose" and ev["decision"]["option"] == 2
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "done" and house.devices["lights"] == "on"
    assert out["receipt"]["evidence"]["channel"] == "transcript"
    assert out["receipt"]["evidence"]["transcriber"] == "alexa"


def test_device_transcripts_obey_every_other_check(gate: Gate, house: House, voice_device: Any, clock: Clock) -> None:
    device = voice_device
    ps = gate.propose("u1", FAN_OPTIONS)
    with pytest.raises(EvidenceRejected):
        gate.submit_transcript(device, ps.pending_id, "wrong-nonce", "one")
    nonce = gate.device_pending(device)[0]["nonce"]
    unclear = gate.submit_transcript(device, ps.pending_id, nonce, "yes")
    assert gate.confirm(ps.pending_id, unclear["evidence_ref"])["status"] == "refused"
    clock.advance(gate.pending_ttl + 1)
    with pytest.raises(EvidenceRejected):
        gate.submit_transcript(device, ps.pending_id, nonce, "one")
    assert house.calls == 0


def test_only_a_device_paired_for_voice_may_send_transcripts(gate: Gate, house: House, device: Any) -> None:
    """Second review, finding 7: any browser could file "voice (Alexa)" evidence with a client-chosen source."""
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    with pytest.raises(EvidenceRejected, match="recognise speech"):
        gate.submit_transcript(device, ps.pending_id, nonce, "one")
    assert house.calls == 0


def test_the_transcriber_named_in_a_receipt_is_the_one_paired(devices: Any, gate: Gate) -> None:
    speaker = devices.pair(devices.start_pairing("u1", transcriber="kitchen-speaker"), name="speaker")
    assert devices.get(speaker.device_id).transcriber == "kitchen-speaker"
    ps = gate.propose("u1", FAN_OPTIONS)
    ev = gate.submit_transcript(speaker, ps.pending_id, gate.device_pending(speaker)[0]["nonce"], "one")
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["receipt"]["evidence"]["transcriber"] == "kitchen-speaker"


def test_an_operator_can_mark_a_paired_device_as_a_voice_device(devices: Any, device: Any) -> None:
    assert devices.set_transcriber(device.device_id, "alexa")
    assert devices.get(device.device_id).transcriber == "alexa"
    assert not devices.set_transcriber("d_nope", "alexa")


def test_withdrawing_open_sets_runs_nothing_and_blocks_later_evidence(gate: Gate, house: House, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1)
    assert gate.withdraw_open("u1") == 1
    assert gate.device_pending(device) == []
    assert gate.confirm(ps.pending_id, ev["evidence_ref"])["status"] == "refused"
    assert house.calls == 0


def test_marking_a_voice_device_never_unrevokes_it(devices: Any, device: Any) -> None:
    """Third review, finding 8: set_transcriber rewrote the device doc and could drop a revocation."""
    assert devices.revoke(device.device_id)
    assert not devices.set_transcriber(device.device_id, "alexa")
    assert devices.get(device.device_id) is None


def test_marking_a_voice_device_is_idempotent(devices: Any, device: Any) -> None:
    assert devices.set_transcriber(device.device_id, "alexa")
    assert devices.set_transcriber(device.device_id, "alexa")  # every deploy runs it again
    assert not devices.set_transcriber(device.device_id, "another")  # a different recogniser needs a re-pair


@pytest.mark.parametrize("given, recorded, sentence", [(None, "touch", "your tap"), ("touch", "touch", "your tap"),
                                                       ("key", "key", "your key press"), ("switch", "switch", "your switch")])
def test_the_receipt_says_exactly_how_the_user_answered(gate: Gate, device: Any, given: Any, recorded: str,
                                                        sentence: str) -> None:
    from confirm_gate.receipt import to_text

    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    kw = {} if given is None else {"input": given}
    ev = gate.submit_tap(device, ps.pending_id, nonce, option=1, **kw)
    out = gate.confirm(ps.pending_id, ev["evidence_ref"])
    assert out["status"] == "done"
    r = out["receipt"]
    assert r["how"]["input"] == recorded and r["evidence"]["input"] == recorded
    assert sentence in to_text(r)
    assert verify_fingerprint(r)


def test_an_unknown_input_kind_is_refused(gate: Gate, device: Any) -> None:
    ps = gate.propose("u1", FAN_OPTIONS)
    nonce = gate.device_pending(device)[0]["nonce"]
    with pytest.raises(EvidenceRejected):
        gate.submit_tap(device, ps.pending_id, nonce, option=1, input="telepathy")
