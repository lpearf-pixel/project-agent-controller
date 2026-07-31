import json
import re
from hashlib import sha256

from project_agent_controller.domain.models import EventRecord

_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_PID = re.compile(r"\b(pid|process_id)\s*[:=]\s*\d+\b", re.IGNORECASE)
_TMP_PATH = re.compile(r"(?<!\w)/(?:tmp|var/tmp)/[^\s]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_message(message: str) -> str:
    value = _TIMESTAMP.sub("<timestamp>", message)
    value = _UUID.sub("<uuid>", value)
    value = _PID.sub(lambda match: f"{match.group(1)}=<pid>", value)
    value = _TMP_PATH.sub("<tmp-path>", value)
    return _WHITESPACE.sub(" ", value).strip()


def fingerprint_event(event: EventRecord) -> str:
    line = str(event.payload.get("line") or event.payload.get("message") or "")
    material = {
        "event_type": event.event_type,
        "source_id": event.source_id,
        "parser": event.payload.get("parser"),
        "error_code": event.payload.get("error_code"),
        "message": normalize_message(line),
        "stack_root": event.payload.get("stack_root"),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"fp-{sha256(encoded.encode('utf-8')).hexdigest()[:24]}"
