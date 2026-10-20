from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from confirm_gate import ActionRegistry, DeviceRegistry, Gate, MemoryStore, SqliteStore


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class House:
    """The state a write can change. Tests assert on it, never on return values alone."""

    def __init__(self) -> None:
        self.devices: dict[str, str] = {"fan": "off", "lights": "off"}
        self.door = "locked"
        self.orders: list[dict[str, Any]] = []
        self.calls = 0

    def snapshot(self) -> tuple[Any, ...]:
        return (dict(self.devices), self.door, list(self.orders), self.calls)


def make_registry(house: House) -> ActionRegistry:
    reg = ActionRegistry()

    @reg.write("home_set", risk="low", preview=lambda a: f"Turn {a['state']} the {a['device']}")
    def home_set(device: str, state: str) -> dict[str, Any]:
        house.calls += 1
        house.devices[device] = state
        return {"device": device, "state": state}

    @reg.write("door_lock", risk=lambda a: "low" if a["locked"] else "high",
               preview=lambda a: "Lock the front door" if a["locked"] else "Unlock the front door")
    def door_lock(locked: bool) -> dict[str, Any]:
        house.calls += 1
        house.door = "locked" if locked else "unlocked"
        return {"door": house.door}

    @reg.write("cart_order", risk="high", preview=lambda a: f"Order {a['qty']} x {a['item']}")
    def cart_order(item: str, qty: int) -> dict[str, Any]:
        house.calls += 1
        house.orders.append({"item": item, "qty": qty})
        return {"ordered": qty}

    @reg.read("home_status")
    def home_status() -> dict[str, Any]:
        return {"devices": dict(house.devices), "door": house.door}

    return reg


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Any) -> Iterator[Any]:
    if request.param == "memory":
        yield MemoryStore()
    else:
        yield SqliteStore(tmp_path / "gate.db")


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def house() -> House:
    return House()


@pytest.fixture()
def gate(store: Any, house: House, clock: Clock) -> Gate:
    return Gate(store, make_registry(house), clock=clock)


@pytest.fixture()
def devices(store: Any, clock: Clock) -> DeviceRegistry:
    return DeviceRegistry(store, clock=clock)


@pytest.fixture()
def device(devices: DeviceRegistry) -> Any:
    code = devices.start_pairing("u1")
    return devices.pair(code, name="test tablet")


FAN_OPTIONS = [
    ("home_set", {"device": "fan", "state": "on"}),
    ("home_set", {"device": "lights", "state": "on"}),
    ("home_set", {"device": "fan", "state": "off"}),
]
