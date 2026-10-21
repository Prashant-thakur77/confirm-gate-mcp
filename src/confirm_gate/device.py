"""HTTP routes for the device channel. Mount them next to ``/mcp``; never give the agent a device key.

All routes but ``/pair`` require a signed request (see :mod:`confirm_gate.evidence`).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .evidence import AuthError, Device, DeviceRegistry, PairingError
from .gate import EvidenceRejected, Gate

MediaSink = Callable[[bytes, Device, str], str]


def signed_path(request: Request) -> str:
    qs = request.scope.get("query_string", b"").decode()
    return request.url.path + (f"?{qs}" if qs else "")


async def authenticate(request: Request, devices: DeviceRegistry) -> tuple[Device, bytes]:
    body = await request.body()
    device = devices.authenticate(request.headers, request.method, signed_path(request), body)
    return device, body


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError("body is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def device_routes(
    gate: Gate,
    devices: DeviceRegistry,
    *,
    transcribe: Callable[[bytes], str],
    transcriber: str | None = None,
    media_sink: MediaSink | None = None,
    prefix: str = "/device",
    max_audio_bytes: int = 2_000_000,
) -> list[Route]:
    async def pair(request: Request) -> JSONResponse:
        try:
            data = _json(await request.body())
            device = devices.pair(str(data.get("code", "")), name=str(data.get("name", ""))[:60])
        except (PairingError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return JSONResponse({"device_id": device.device_id, "user_id": device.user_id,
                             "key": device.key, "name": device.name})

    async def pending(request: Request) -> JSONResponse:
        try:
            device, _ = await authenticate(request, devices)
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        items = await anyio.to_thread.run_sync(gate.device_pending, device)
        return JSONResponse({"pending": items})

    async def evidence(request: Request) -> JSONResponse:
        try:
            device, body = await authenticate(request, devices)
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        try:
            data = _json(body)
            pid, nonce, kind = str(data.get("pending_id", "")), str(data.get("nonce", "")), data.get("kind")
            if kind == "tap":
                out = await anyio.to_thread.run_sync(
                    lambda: gate.submit_tap(device, pid, nonce, option=data.get("option"), answer=data.get("answer")))
            elif kind == "voice":
                raw = str(data.get("audio_b64", ""))
                if len(raw) > (max_audio_bytes * 4) // 3 + 4:  # checked before decoding: no memory blow-up
                    return JSONResponse({"error": f"audio is larger than {max_audio_bytes} bytes"}, status_code=413)
                try:
                    audio = base64.b64decode(raw, validate=True)
                except binascii.Error as exc:
                    raise ValueError("audio_b64 is not base64") from exc
                ref = media_sink(audio, device, pid) if media_sink else None
                out = await anyio.to_thread.run_sync(
                    lambda: gate.submit_voice(device, pid, nonce, audio, transcribe=transcribe,
                                              media_ref=ref, transcriber=transcriber))
            elif kind == "transcript":
                out = await anyio.to_thread.run_sync(
                    lambda: gate.submit_transcript(device, pid, nonce, str(data.get("transcript", ""))))
            else:
                raise ValueError("kind must be tap, voice or transcript")
        except EvidenceRejected as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(out)

    async def confirm(request: Request) -> JSONResponse:
        try:
            _, body = await authenticate(request, devices)
            data = _json(body)
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        out = await gate.aconfirm(data.get("pending_id"), data.get("evidence_ref"))
        return JSONResponse(out)

    async def receipts(request: Request) -> JSONResponse:
        try:
            device, _ = await authenticate(request, devices)
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        rows = await anyio.to_thread.run_sync(gate.receipts, device.user_id)
        return JSONResponse({"receipts": rows})

    return [
        Route(f"{prefix}/pair", pair, methods=["POST"]),
        Route(f"{prefix}/pending", pending, methods=["GET"]),
        Route(f"{prefix}/evidence", evidence, methods=["POST"]),
        Route(f"{prefix}/confirm", confirm, methods=["POST"]),
        Route(f"{prefix}/receipts", receipts, methods=["GET"]),
    ]
