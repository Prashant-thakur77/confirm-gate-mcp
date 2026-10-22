"""The consent receipt: why one change was allowed to happen.

Months later someone asks *who told it to do that*. "The model decided to" is
not an answer, so every executed write leaves a receipt built only from records
the gate already holds:

* **who** — the user and the paired device that carried the evidence
* **what** — the exact action and arguments, and the card text the user saw
* **when** — proposed, evidence received, confirmed, executed
* **how** — voice or tap; for voice, the server's own transcript of the audio
  and the audio's fingerprint, so the words can be checked against the recording
* **checks** — each gate check by name, all of which had to pass

The fingerprint detects a receipt edited after it was issued. It is a digest,
not a signature, and says so.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

SCHEMA = "confirm-gate.receipt/1"


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="milliseconds") if ts else None


def build(
    *,
    pending: dict[str, Any],
    option: dict[str, Any],
    evidence: dict[str, Any],
    checks: list[dict[str, Any]],
    executed_at: float,
    result: Any,
    chain: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "who": {"user_id": pending["user_id"], "device_id": evidence["device_id"]},
        "what": {
            "action": option["action"],
            "args": option["args"],
            "shown_as": option["text"],
            "risk": option["risk"],
            "params_hash": option["params_hash"],
            "origin": pending.get("origin", "user_words"),
            "options_shown": len(pending["options"]),
            "option_chosen": option["n"],
        },
        "when": {
            "proposed_at": _iso(pending["issued_at"]),
            "evidence_at": _iso(evidence["created_at"]),
            "confirmed_at": _iso(executed_at),
            "executed_at": _iso(executed_at),
        },
        "how": {
            "channel": evidence["kind"],
            "input": evidence.get("input") or ("voice" if evidence["kind"] in ("voice", "transcript") else "touch"),
            "pending_id": pending["pending_id"],
            "nonce_sha256": hashlib.sha256(pending["nonce"].encode()).hexdigest()[:12],
        },
        "evidence": {
            "evidence_id": evidence["evidence_id"],
            "channel": evidence["kind"],
            "input": evidence.get("input") or ("voice" if evidence["kind"] in ("voice", "transcript") else "touch"),
            "transcript": evidence.get("transcript"),
            "decision": evidence["decision"],
            "fingerprint": evidence["fingerprint"],
            "media_ref": evidence.get("media_ref"),
            "transcriber": evidence.get("transcriber"),
        },
        "context": {k: v for k, v in (pending.get("context") or {}).items() if k in ("heard", "asr", "utterance_ref")},
        "checks": checks,
        "result": result,
    }
    if chain:
        body["chain"] = chain
    body["receipt_id"] = fingerprint(body)
    return body


def fingerprint(body: dict[str, Any]) -> str:
    content = {k: v for k, v in body.items() if k != "receipt_id"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return "rcpt_" + hashlib.sha256(canonical.encode()).hexdigest()[:20]


def verify_fingerprint(receipt: dict[str, Any]) -> bool:
    return receipt.get("receipt_id") == fingerprint(receipt)


def to_text(receipt: dict[str, Any]) -> str:
    """One sentence a screen reader or speaker can say."""
    how = {"voice": "your voice", "transcript": "your voice"}.get(receipt["how"]["channel"]) or {
        "key": "your key press", "switch": "your switch"}.get(receipt["how"].get("input", ""), "your tap")
    said = receipt["evidence"].get("transcript")
    heard = f' (heard "{said}")' if said else ""
    return f"Done: {receipt['what']['shown_as']}. Confirmed by {how}{heard}."


def to_markdown(receipt: dict[str, Any]) -> str:
    w, e = receipt["what"], receipt["evidence"]
    lines = [
        "### Consent receipt",
        "",
        f"- **Did** {w['shown_as']} (`{w['action']}` `{json.dumps(w['args'], sort_keys=True)}`, risk {w['risk']})",
        f"- **Chosen** option {w['option_chosen']} of {w['options_shown']}, suggested by "
        f"{'the user' if w['origin'] == 'user_words' else 'the assistant'}",
        f"- **Confirmed by** {receipt['how']['channel']} on device `{receipt['who']['device_id']}`"
        + (f', heard "{e["transcript"]}"' if e.get("transcript") else ""),
        f"- **When** proposed {receipt['when']['proposed_at']}, done {receipt['when']['executed_at']}",
        f"- **Checks** {sum(c['ok'] for c in receipt['checks'])}/{len(receipt['checks'])} passed",
        f"- **Evidence** `{e['fingerprint'][:16]}`" + (f" · `{e['media_ref']}`" if e.get("media_ref") else ""),
        "",
        f"`{receipt['receipt_id']}`",
    ]
    return "\n".join(lines)
