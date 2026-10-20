"""The actions a gate knows about: reads run freely, writes only through ``Gate.confirm``."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Risk = Literal["low", "high"]


def params_hash(action: str, args: dict[str, Any]) -> str:
    """Stable identity of (action, args), so what ran is provably what was shown."""
    canonical = json.dumps({"action": action, "args": args}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Action:
    name: str
    kind: Literal["read", "write"]
    fn: Callable[..., Any]
    risk: Risk | Callable[[dict[str, Any]], Risk] = "low"
    preview: Callable[[dict[str, Any]], str] | None = None
    description: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def risk_for(self, args: dict[str, Any]) -> Risk:
        return self.risk(args) if callable(self.risk) else self.risk

    def preview_for(self, args: dict[str, Any]) -> str:
        if self.preview is not None:
            return self.preview(args)
        shown = ", ".join(f"{k}={v!r}" for k, v in sorted(args.items()))
        return f"{self.name}({shown})"

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def add(self, action: Action) -> Action:
        if action.name in self._actions:
            raise ValueError(f"action {action.name!r} is already registered")
        self._actions[action.name] = action
        return action

    def write(
        self,
        name: str | None = None,
        *,
        risk: Risk | Callable[[dict[str, Any]], Risk] = "low",
        preview: Callable[[dict[str, Any]], str] | None = None,
        **meta: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add(Action(name or fn.__name__, "write", fn, risk, preview, inspect.getdoc(fn) or "", meta))
            return fn

        return deco

    def read(self, name: str | None = None, **meta: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add(Action(name or fn.__name__, "read", fn, "low", None, inspect.getdoc(fn) or "", meta))
            return fn

        return deco

    def get(self, name: str) -> Action | None:
        return self._actions.get(name)

    def writes(self) -> list[Action]:
        return [a for a in self._actions.values() if a.kind == "write"]

    def reads(self) -> list[Action]:
        return [a for a in self._actions.values() if a.kind == "read"]

    def __contains__(self, name: str) -> bool:
        return name in self._actions
