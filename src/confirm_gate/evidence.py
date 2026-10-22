"""Paired devices and signed requests: how the gate knows evidence came from the user.

A device is paired once, with a short code shown out of band (printed by the
server, or shown on a screen the user can see). Pairing hands the device a
secret key. Every later device request is signed with it::

    signature = HMAC-SHA256(key, METHOD \\n PATH \\n TIMESTAMP \\n SHA256(BODY))

The agent never holds a device key, so it cannot read a nonce from the device
channel or submit evidence through it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .store import Store


class AuthError(Exception):
    """A device request whose signature does not hold."""


class PairingError(Exception):
    """A pairing code that is wrong, used, expired or locked."""


@dataclass(frozen=True)
class Device:
    device_id: str
    user_id: str
    key: str
    name: str = ""
    transcriber: str = ""  # set only for a device that recognises speech itself (paired as such by the operator)

    def public(self) -> dict[str, Any]:
        return {"device_id": self.device_id, "user_id": self.user_id, "name": self.name}


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def sign_request(key: str, method: str, path: str, timestamp: int, body: bytes = b"") -> str:
    message = f"{method.upper()}\n{path}\n{int(timestamp)}\n{body_hash(body)}".encode()
    return hmac.new(base64.urlsafe_b64decode(_pad(key)), message, hashlib.sha256).hexdigest()


def _pad(key: str) -> str:
    return key + "=" * (-len(key) % 4)


class DeviceRegistry:
    pairing_ttl = 600
    max_attempts = 5  # wrong pairing codes per window from one source (the caller's address)
    global_max_attempts = 30  # wrong codes per window from everyone together: bounds guessing from many addresses
    attempt_window = 600
    skew = 60

    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    # pairing -----------------------------------------------------------------
    def start_pairing(self, user_id: str, *, transcriber: str = "", minted_by: str = "") -> str:
        """A fresh 6-digit code for *user_id*; show it to the user, never to the agent.

        *transcriber* names the speech recogniser of a device that sends its own transcripts (a smart-speaker
        skill). Only a device paired with one may submit transcript evidence, and receipts name that recogniser.
        *minted_by* is the paired device that asked for the code (adding a screen from a phone): the code stops
        working if that device is unpaired first, and the new device records where it came from.
        """
        for _ in range(20):
            code = f"{secrets.randbelow(10**6):06d}"
            doc = {"user_id": user_id, "expires_at": self.clock() + self.pairing_ttl, "used_at": None,
                   "transcriber": transcriber[:40], "minted_by": minted_by}
            if self.store.put_if_absent("pairing", code, doc):
                return code
        raise PairingError("could not allocate a pairing code")

    def _window(self) -> tuple[str, float]:
        w = int(self.clock() // self.attempt_window)
        return f"w{w}", float((w + 1) * self.attempt_window)

    def pair(self, code: str, *, name: str = "", source: str = "") -> Device:
        """Exchange a pairing code for a device key.

        Every attempt takes one unit of two time-windowed budgets *before* the code is looked up: one for its
        *source* (the caller's address) and one shared by everyone (atomic counters, so a burst of parallel guesses
        cannot outrun them). A right code gives both units back. Nothing resets a budget except its window passing,
        so starting a new pairing never hands a guesser fresh tries, and one guesser cannot lock out the owner.
        """
        window, ends = self._window()
        keys = [(f"{window}:src:{source}", self.max_attempts), (window, self.global_max_attempts)]
        taken = []
        for key, limit in keys:
            taken.append(key)
            if self.store.add("pairing_attempts", key, 1, expires_at=ends) > limit:
                raise PairingError("too many wrong codes; try again in a few minutes")
        doc = self.store.get("pairing", str(code))
        if doc is None or doc.get("used_at") is not None or doc["expires_at"] < self.clock():
            raise PairingError("that code is wrong or has expired")
        minter = str(doc.get("minted_by") or "")
        if minter and self.get(minter) is None:
            raise PairingError("that code is wrong or has expired")  # the device that made it was unpaired
        if not self.store.set_once("pairing", str(code), "used_at", self.clock()):
            raise PairingError("that code has already been used")
        for key in taken:
            self.store.add("pairing_attempts", key, -1, expires_at=ends)
        return self.issue(doc["user_id"], name=name, transcriber=str(doc.get("transcriber") or ""), paired_via=minter)

    def issue(self, user_id: str, *, name: str = "", transcriber: str = "", paired_via: str = "") -> Device:
        """Create a device for *user_id* directly. Only for callers that already know who the user is
        (an operator's terminal, a fresh sandbox user); everyone else pairs with a code."""
        device = Device(
            device_id="d_" + secrets.token_hex(8),
            user_id=user_id,
            key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="),
            name=name[:60],
            transcriber=transcriber[:40],
        )
        self.store.put("device", device.device_id, {**asdict(device), "paired_at": self.clock(), "revoked_at": None,
                                                    **({"paired_via": paired_via} if paired_via else {})})
        return device

    def set_transcriber(self, device_id: str, transcriber: str) -> bool:
        """Operator action: mark an already paired device as one that recognises speech itself.

        A one-time, conditional write (never a rewrite of the device), so it cannot undo a revocation that lands at
        the same moment. Idempotent for the same recogniser; a different one needs a fresh pairing.
        """
        doc = self.store.get("device", device_id)
        if doc is None or doc.get("revoked_at") is not None:
            return False
        if doc.get("transcriber"):
            return doc["transcriber"] == transcriber[:40]
        return self.store.set_once("device", device_id, "transcriber_at", self.clock(), {"transcriber": transcriber[:40]})

    def revoke(self, device_id: str) -> bool:
        return self.store.set_once("device", device_id, "revoked_at", self.clock())

    def get(self, device_id: str) -> Device | None:
        doc = self.store.get("device", device_id)
        if doc is None or doc.get("revoked_at") is not None:
            return None
        return Device(doc["device_id"], doc["user_id"], doc["key"], doc.get("name", ""), doc.get("transcriber") or "")

    # request authentication -----------------------------------------------------
    def authenticate(self, headers: Mapping[str, str], method: str, path: str, body: bytes) -> Device:
        h = {k.lower(): v for k, v in headers.items()}
        device_id, ts, sig = h.get("x-device-id"), h.get("x-timestamp"), h.get("x-signature")
        if not (device_id and ts and sig):
            raise AuthError("unsigned request")
        device = self.get(device_id)
        if device is None:
            raise AuthError("unknown or revoked device")
        try:
            t = int(ts)
        except ValueError as exc:
            raise AuthError("bad timestamp") from exc
        if abs(self.clock() - t) > self.skew:
            raise AuthError("stale request")
        expected = sign_request(device.key, method, path, t, body)
        if not hmac.compare_digest(expected, sig):
            raise AuthError("bad signature")
        if not self.store.put_if_absent("signature", sig, {"device_id": device_id, "expires_at": t + self.skew}):
            raise AuthError("replayed request")
        return device
