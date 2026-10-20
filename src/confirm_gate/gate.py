"""The gate: writes wait as pending card sets until the user's own evidence releases one.

Lifecycle of one write::

    propose()            → a card set with 1-3 options and a one-time nonce (stored, never returned to MCP)
    device_pending()     → the paired device reads the set *with* its nonce
    submit_tap/voice()   → the device sends evidence quoting the nonce; the gate decodes it
    confirm()            → every check passes, the nonce is consumed atomically, the option runs,
                           a receipt is written

``confirm`` may be called by anyone — the agent included — because it can only
release what the user's evidence chose. An agent holding the ``confirm`` tool but
no evidence can do nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import anyio

from . import receipt as receipts
from .actions import Action, ActionRegistry, params_hash
from .consent import decide
from .evidence import Device
from .store import Store

logger = logging.getLogger("confirm_gate")

Origin = Literal["user_words", "assistant"]
OptionSpec = tuple[str, dict[str, Any]] | tuple[str, dict[str, Any], str]


class EvidenceRejected(Exception):
    """Evidence the gate will not record (wrong nonce, closed set, replayed audio...)."""


@dataclass(frozen=True)
class PendingSet:
    pending_id: str
    user_id: str
    nonce: str
    stage: str
    origin: str
    options: list[dict[str, Any]]
    issued_at: float
    expires_at: float

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> PendingSet:
        return cls(doc["pending_id"], doc["user_id"], doc["nonce"], doc["stage"], doc["origin"],
                   doc["options"], doc["issued_at"], doc["expires_at"])


@dataclass
class _Plan:
    pending: dict[str, Any]
    evidence: dict[str, Any]
    option: dict[str, Any]
    action: Action
    checks: list[dict[str, Any]]
    now: float


class Gate:
    pending_ttl = 120.0
    final_ttl = 60.0
    evidence_ttl = 120.0

    def __init__(
        self,
        store: Store,
        registry: ActionRegistry,
        *,
        clock: Callable[[], float] = time.time,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.clock = clock
        self.on_event = on_event

    # events ------------------------------------------------------------------
    def _emit(self, kind: str, **fields: Any) -> None:
        event = {"type": kind, "at": self.clock(), **fields}
        logger.info("gate %s %s", kind, {k: v for k, v in fields.items() if k != "options"})
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # an observer must never break the gate
                logger.exception("on_event hook failed")

    # proposing ---------------------------------------------------------------
    def propose(
        self,
        user_id: str,
        options: Sequence[OptionSpec],
        *,
        origin: Origin = "user_words",
        context: dict[str, Any] | None = None,
        stage: str | None = None,
        parent_id: str | None = None,
    ) -> PendingSet:
        if not 1 <= len(options) <= 5:
            raise ValueError("a card set needs 1 to 5 options")
        built: list[dict[str, Any]] = []
        for i, spec in enumerate(options, start=1):
            name, args = spec[0], dict(spec[1])
            action = self.registry.get(name)
            if action is None:
                raise ValueError(f"unknown action {name!r}")
            if action.kind != "write":
                raise ValueError(f"{name!r} is a read; reads need no confirmation")
            text = spec[2] if len(spec) > 2 else action.preview_for(args)  # type: ignore[misc]
            built.append({"n": i, "action": name, "args": args, "text": text,
                          "risk": action.risk_for(args), "params_hash": params_hash(name, args)})
        if stage is None:
            stage = "final" if len(built) == 1 and built[0]["risk"] == "high" else "choose"
        now = self.clock()
        doc = {
            "pending_id": "p_" + secrets.token_hex(8),
            "user_id": user_id,
            "nonce": secrets.token_urlsafe(16),
            "stage": stage,
            "origin": origin,
            "options": built,
            "issued_at": now,
            "expires_at": now + (self.final_ttl if stage == "final" else self.pending_ttl),
            "consumed_at": None,
            "outcome": None,
            "context": dict(context or {}),
            "parent_id": parent_id,
        }
        self.store.put("pending", doc["pending_id"], doc)
        self._emit("proposed", pending_id=doc["pending_id"], user_id=user_id, origin=origin, stage=stage,
                   options=[{k: o[k] for k in ("n", "text", "action", "risk")} for o in built])
        return PendingSet.from_doc(doc)

    def _state(self, doc: dict[str, Any]) -> str:
        if doc.get("consumed_at") is not None:
            return str(doc.get("outcome") or "answered")
        if doc["expires_at"] < self.clock():
            return "expired"
        return "waiting_for_user"

    def public_view(self, pending: PendingSet | dict[str, Any]) -> dict[str, Any]:
        """What the agent and any MCP client may see. Never the nonce."""
        doc = pending if isinstance(pending, dict) else self.store.get("pending", pending.pending_id)
        if doc is None:
            raise KeyError("unknown pending set")
        return {
            "pending_id": doc["pending_id"],
            "state": self._state(doc),
            "stage": doc["stage"],
            "origin": doc["origin"],
            "options": [{k: o[k] for k in ("n", "text", "action", "args", "risk")} for o in doc["options"]],
            "expires_in_s": max(0, round(doc["expires_at"] - self.clock())),
        }

    def pending_status(self, pending_id: str) -> dict[str, Any]:
        doc = self.store.get("pending", str(pending_id or ""))
        if doc is None:
            return {"pending_id": pending_id, "state": "unknown"}
        return self.public_view(doc)

    # the device side ---------------------------------------------------------------
    def _device_ok(self, device: Device) -> dict[str, Any]:
        doc = self.store.get("device", device.device_id)
        if doc is None or doc.get("revoked_at") is not None or doc.get("user_id") != device.user_id:
            raise EvidenceRejected("this device is not paired")
        return doc

    def device_pending(self, device: Device) -> list[dict[str, Any]]:
        """Open card sets for the device's user, with their nonces. Device channel only."""
        try:
            self._device_ok(device)
        except EvidenceRejected:
            return []
        now = self.clock()
        docs = [d for d in self.store.scan("pending", user_id=device.user_id)
                if d.get("consumed_at") is None and d["expires_at"] >= now]
        docs.sort(key=lambda d: d["issued_at"])
        return [{**self.public_view(d), "nonce": d["nonce"], "context": d.get("context") or {}} for d in docs]

    def device_view(self, device: Device, pending_id: str) -> dict[str, Any] | None:
        """One open card set with its nonce, read consistently, if it belongs to the device's user."""
        try:
            self._device_ok(device)
        except EvidenceRejected:
            return None
        d = self.store.get("pending", str(pending_id or ""))
        if d is None or d["user_id"] != device.user_id or d.get("consumed_at") is not None \
                or d["expires_at"] < self.clock():
            return None
        return {**self.public_view(d), "nonce": d["nonce"], "context": d.get("context") or {}}

    def _open_for(self, device: Device, pending_id: str, nonce: str) -> dict[str, Any]:
        self._device_ok(device)
        doc = self.store.get("pending", str(pending_id or ""))
        if doc is None or doc["user_id"] != device.user_id:
            raise EvidenceRejected("no such card set for this user")
        if not hmac.compare_digest(str(nonce or ""), doc["nonce"]):
            raise EvidenceRejected("the nonce does not match this card set")
        if doc.get("consumed_at") is not None:
            raise EvidenceRejected("this card set has already been answered")
        if doc["expires_at"] < self.clock():
            raise EvidenceRejected("this card set has expired")
        return doc

    def _record(self, device: Device, doc: dict[str, Any], kind: str, decision: dict[str, Any],
                fingerprint: str, evidence_id: str, **extra: Any) -> dict[str, Any]:
        now = self.clock()
        option_hash = None
        if decision["kind"] in ("choose", "yes"):
            n = decision.get("option") or 1
            if 1 <= n <= len(doc["options"]):
                option_hash = doc["options"][n - 1]["params_hash"]
        ev = {
            "evidence_id": evidence_id,
            "pending_id": doc["pending_id"],
            "user_id": doc["user_id"],
            "device_id": device.device_id,
            "kind": kind,
            "decision": decision,
            "fingerprint": fingerprint,
            "option_hash": option_hash,
            "created_at": now,
            "expires_at": min(now + self.evidence_ttl, doc["expires_at"]),
            "used_at": None,
            **extra,
        }
        self.store.put("evidence", evidence_id, ev)
        self._emit("evidence", pending_id=doc["pending_id"], evidence_id=evidence_id, channel=kind,
                   decision=decision, transcript=extra.get("transcript"))
        return {"evidence_ref": evidence_id, "decision": decision}

    def submit_tap(self, device: Device, pending_id: str, nonce: str, *, option: int | None = None,
                   answer: str | None = None) -> dict[str, Any]:
        doc = self._open_for(device, pending_id, nonce)
        n = len(doc["options"])
        if answer == "no":
            decision = {"kind": "no", "option": None, "heard": "tap: none of these"}
        elif doc["stage"] == "final":
            if answer != "yes" and option != 1:
                raise EvidenceRejected("the final card takes yes or no")
            decision = {"kind": "yes", "option": None, "heard": "tap: yes"}
        else:
            if not isinstance(option, int) or isinstance(option, bool) or not 1 <= option <= n:
                raise EvidenceRejected(f"choose a card from 1 to {n}")
            decision = {"kind": "choose", "option": option, "heard": f"tap: {option}"}
        evidence_id = "ev_" + secrets.token_hex(12)
        payload = f"tap|{device.device_id}|{doc['pending_id']}|{decision['kind']}|{decision['option']}|{evidence_id}"
        fp = hashlib.sha256(payload.encode()).hexdigest()
        if not self.store.put_if_absent("fingerprint", fp, {"evidence_id": evidence_id}):
            raise EvidenceRejected("this evidence has already been used")
        return self._record(device, doc, "tap", decision, fp, evidence_id)

    def submit_voice(self, device: Device, pending_id: str, nonce: str, audio: bytes, *,
                     transcribe: Callable[[bytes], str], media_ref: str | None = None,
                     transcriber: str | None = None) -> dict[str, Any]:
        doc = self._open_for(device, pending_id, nonce)
        if not audio:
            raise EvidenceRejected("no audio")
        fp = hashlib.sha256(audio).hexdigest()
        evidence_id = "ev_" + secrets.token_hex(12)
        if not self.store.put_if_absent("fingerprint", fp, {"evidence_id": evidence_id, "at": self.clock()}):
            raise EvidenceRejected("this recording has already been used")
        try:
            transcript = transcribe(audio) or ""
        except Exception as exc:
            raise EvidenceRejected(f"could not transcribe the recording: {exc}") from exc
        d = decide(transcript, len(doc["options"]), stage=doc["stage"]).as_dict()
        return self._record(device, doc, "voice", d, fp, evidence_id, transcript=transcript,
                            media_ref=media_ref, transcriber=transcriber)

    # confirming ------------------------------------------------------------------
    def _refuse(self, pending_id: Any, reason: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
        self._emit("refused", pending_id=pending_id, reason=reason, checks=checks)
        return {"status": "refused", "pending_id": pending_id, "reason": reason, "checks": checks}

    def _prepare(self, pending_id: Any, evidence_ref: Any) -> tuple[dict[str, Any] | None, _Plan | None]:
        checks: list[dict[str, Any]] = []
        now = self.clock()

        def check(name: str, ok: bool) -> bool:
            checks.append({"name": name, "ok": bool(ok)})
            return bool(ok)

        ref = evidence_ref if isinstance(evidence_ref, str) else ""
        ev = self.store.get("evidence", ref) if ref.startswith("ev_") else None
        if not check("user_evidence_exists", ev is not None):
            return self._refuse(pending_id, "no user evidence for this card set: only the user's own voice "
                                            "or tap on their paired device can confirm", checks), None
        assert ev is not None
        if not check("evidence_is_for_this_card_set", ev["pending_id"] == pending_id):
            return self._refuse(pending_id, "that evidence answers a different card set", checks), None
        doc = self.store.get("pending", str(pending_id))
        if not check("card_set_exists", doc is not None):
            return self._refuse(pending_id, "unknown card set", checks), None
        assert doc is not None
        dev = self.store.get("device", ev["device_id"])
        if not check("from_a_paired_device", dev is not None and dev.get("revoked_at") is None
                     and dev.get("user_id") == doc["user_id"] == ev["user_id"]):
            return self._refuse(pending_id, "the evidence did not come from this user's paired device", checks), None
        if not check("card_set_still_open", doc.get("consumed_at") is None):
            return self._refuse(pending_id, "this card set has already been answered", checks), None
        if not check("card_set_not_expired", now <= doc["expires_at"]):
            return self._refuse(pending_id, "the card set expired before it was confirmed", checks), None
        if not check("evidence_not_expired", now <= ev["expires_at"]):
            return self._refuse(pending_id, "the evidence expired", checks), None
        if not check("evidence_not_used", ev.get("used_at") is None):
            return self._refuse(pending_id, "that evidence has already been used", checks), None

        decision = ev["decision"]
        if decision["kind"] == "no":
            if self.store.set_once("pending", doc["pending_id"], "consumed_at", now,
                                   {"outcome": "cancelled", "evidence_id": ev["evidence_id"]}):
                self.store.set_once("evidence", ev["evidence_id"], "used_at", now)
                self._emit("cancelled", pending_id=doc["pending_id"], evidence_id=ev["evidence_id"])
                return {"status": "cancelled", "pending_id": doc["pending_id"],
                        "said": decision.get("heard"), "message": "Cancelled. Nothing was changed."}, None
            return self._refuse(pending_id, "this card set has already been answered", checks), None

        n = len(doc["options"])
        if doc["stage"] == "final":
            accepted = decision["kind"] == "yes"
            option_n = 1
        else:
            accepted = decision["kind"] == "choose" and isinstance(decision.get("option"), int) \
                and 1 <= decision["option"] <= n
            option_n = decision.get("option") or 0
        if not check("user_words_choose_a_card", accepted):
            heard = decision.get("heard") or ""
            want = "yes or no" if doc["stage"] == "final" else f"a number from 1 to {n}"
            return self._refuse(pending_id, f'the words heard ("{heard}") did not clearly give {want}', checks), None

        option = doc["options"][option_n - 1]
        unchanged = (params_hash(option["action"], option["args"]) == option["params_hash"] == ev.get("option_hash"))
        if not check("action_unchanged_since_shown", unchanged):
            return self._refuse(pending_id, "the action changed after the user chose it", checks), None
        action = self.registry.get(option["action"])
        if not check("action_is_registered_write", action is not None and action.kind == "write"):
            return self._refuse(pending_id, "that action is not a registered write", checks), None
        assert action is not None

        if not check("nonce_consumed_once", self.store.set_once(
                "pending", doc["pending_id"], "consumed_at", now,
                {"outcome": "confirmed", "evidence_id": ev["evidence_id"], "option": option_n})):
            return self._refuse(pending_id, "this card set has already been answered", checks), None
        if not check("evidence_consumed_once", self.store.set_once("evidence", ev["evidence_id"], "used_at", now)):
            return self._refuse(pending_id, "that evidence has already been used", checks), None

        if doc["stage"] == "choose" and option["risk"] == "high":
            final = self.propose(doc["user_id"], [(option["action"], option["args"], option["text"])],
                                 origin=doc["origin"], context={**(doc.get("context") or {}),
                                 "first_stage": {"pending_id": doc["pending_id"], "evidence_id": ev["evidence_id"],
                                                 "heard": decision.get("heard")}},
                                 stage="final", parent_id=doc["pending_id"])
            self._emit("needs_final_yes", pending_id=final.pending_id, parent_id=doc["pending_id"])
            return {"status": "needs_final_yes", "pending_id": final.pending_id,
                    "card": self.public_view(final),
                    "message": f"{option['text']}? This one needs a second yes."}, None
        return None, _Plan(doc, ev, option, action, checks, now)

    def _finish(self, plan: _Plan, result: Any = None, error: BaseException | None = None) -> dict[str, Any]:
        if error is not None:
            self._emit("failed", pending_id=plan.pending["pending_id"], error=str(error))
            return {"status": "failed", "pending_id": plan.pending["pending_id"], "error": str(error),
                    "message": f"I tried to {plan.option['text'].lower()} but it failed: {error}"}
        chain = None
        first = (plan.pending.get("context") or {}).get("first_stage")
        if first:
            chain = [first]
        rec = receipts.build(pending=plan.pending, option=plan.option, evidence=plan.evidence,
                             checks=plan.checks, executed_at=self.clock(), result=result, chain=chain)
        self.store.put("receipt", rec["receipt_id"], {**rec, "user_id": plan.pending["user_id"],
                                                     "sort_at": self.clock()})
        self._emit("done", pending_id=plan.pending["pending_id"], receipt_id=rec["receipt_id"],
                   text=plan.option["text"])
        return {"status": "done", "pending_id": plan.pending["pending_id"], "result": result,
                "receipt": rec, "message": receipts.to_text(rec)}

    @staticmethod
    def _call_args(plan: _Plan) -> dict[str, Any]:
        kwargs = dict(plan.option["args"])
        if plan.action.meta.get("pass_user"):
            kwargs["user_id"] = plan.pending["user_id"]  # the user whose card set this is, never an argument
        return kwargs

    def confirm(self, pending_id: Any, evidence_ref: Any = None) -> dict[str, Any]:
        """Run the chosen option if, and only if, the user's evidence releases it."""
        done, plan = self._prepare(pending_id, evidence_ref)
        if done is not None:
            return done
        assert plan is not None
        kwargs = self._call_args(plan)
        try:
            if plan.action.is_async:
                result = asyncio.run(plan.action.fn(**kwargs))
            else:
                result = plan.action.fn(**kwargs)
        except Exception as exc:
            return self._finish(plan, error=exc)
        return self._finish(plan, result)

    async def aconfirm(self, pending_id: Any, evidence_ref: Any = None) -> dict[str, Any]:
        done, plan = await anyio.to_thread.run_sync(self._prepare, pending_id, evidence_ref)
        if done is not None:
            return done
        assert plan is not None
        kwargs = self._call_args(plan)
        try:
            if plan.action.is_async:
                result = await plan.action.fn(**kwargs)
            else:
                result = await anyio.to_thread.run_sync(lambda: plan.action.fn(**kwargs))
        except Exception as exc:
            return self._finish(plan, error=exc)
        return await anyio.to_thread.run_sync(self._finish, plan, result)

    def receipts(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.store.scan("receipt", user_id=user_id)
        rows.sort(key=lambda r: r.get("sort_at", 0), reverse=True)
        return [{k: v for k, v in r.items() if k not in ("user_id", "sort_at")} for r in rows[:limit]]
