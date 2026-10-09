import json
from typing import Any


def envelope_ok(command: str, **counts: Any) -> dict[str, Any]:
    return {"ok": True, "command": command, **counts}


def envelope_err(command: str, code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "command": command, "error": {"code": code, "message": message}}


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def exit_code_for(ok: bool, partial: bool = False) -> int:
    if not ok:
        return 1
    return 2 if partial else 0
