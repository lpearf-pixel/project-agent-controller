import re

from pydantic import BaseModel, ConfigDict


class RedactionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    matches: tuple[str, ...]
    safe_to_export: bool


class Redactor:
    def __init__(self) -> None:
        self.patterns: tuple[tuple[str, re.Pattern[str], str], ...] = (
            (
                "authorization",
                re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+"),
                r"\1<redacted>",
            ),
            (
                "private_key",
                re.compile(
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
                    r"-----END [A-Z ]*PRIVATE KEY-----",
                    re.DOTALL,
                ),
                "<redacted-private-key>",
            ),
            (
                "token",
                re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})\b"),
                "<redacted-token>",
            ),
            (
                "email",
                re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
                "<redacted-email>",
            ),
            ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<redacted-phone>"),
            (
                "home_path",
                re.compile(r"/(?:Users|home)/[^/\s]+"),
                "/<home>/<redacted-user>",
            ),
        )

    def redact(self, text: str) -> RedactionResult:
        controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
        if "\x00" in text or "\ufffd" in text or controls > 0:
            return RedactionResult(
                text=text,
                matches=("unsafe_control",),
                safe_to_export=False,
            )

        redacted = text
        matches: list[str] = []
        for name, pattern, replacement in self.patterns:
            redacted, count = pattern.subn(replacement, redacted)
            if count:
                matches.append(name)
        return RedactionResult(
            text=redacted,
            matches=tuple(sorted(set(matches))),
            safe_to_export=True,
        )
