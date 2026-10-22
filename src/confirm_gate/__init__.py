"""confirm-gate-mcp: confirm-before-act for MCP.

Write tools become pending card sets. Only evidence from the user's own paired
device (their voice or tap, quoting a one-time nonce the agent never sees) can
release one.
"""

from .actions import Action, ActionRegistry, params_hash
from .cli import build_app
from .consent import Decision, decide, normalise
from .device import device_routes
from .evidence import AuthError, Device, DeviceRegistry, PairingError, sign_request
from .gate import EvidenceRejected, Gate, NotWaiting, PendingSet
from .proxy import GatedServer, wrap_upstream
from .store import DynamoStore, MemoryStore, SqliteStore, Store

__all__ = [
    "Action", "ActionRegistry", "AuthError", "Decision", "Device", "DeviceRegistry", "DynamoStore",
    "EvidenceRejected", "Gate", "GatedServer", "MemoryStore", "NotWaiting", "PairingError", "PendingSet", "SqliteStore",
    "Store", "build_app", "decide", "device_routes", "normalise", "params_hash", "sign_request", "wrap_upstream",
]
__version__ = "0.8.0"
