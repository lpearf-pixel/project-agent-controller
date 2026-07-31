from __future__ import annotations

import os
import re
import stat
from collections.abc import MutableMapping
from pathlib import Path

from dotenv import dotenv_values

_MAX_ENV_FILE_BYTES = 65_536
_PAC_KEY = re.compile(r"^PAC_[A-Z0-9_]+$")
_PROXY_KEYS = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
)


def load_service_environment(
    environ: MutableMapping[str, str] | None = None,
) -> Path | None:
    target = environ if environ is not None else os.environ
    configured = target.get("PAC_ENV_FILE")
    if configured is None or not configured.strip():
        return None

    path = Path(configured)
    if not path.is_absolute():
        raise ValueError("PAC_ENV_FILE must be an absolute path")
    if not path.is_file():
        raise ValueError("PAC_ENV_FILE must reference a regular file")
    file_stat = path.stat()
    if os.name == "posix" and stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise ValueError("PAC_ENV_FILE must have mode 0600 or stricter")
    if file_stat.st_size > _MAX_ENV_FILE_BYTES:
        raise ValueError(f"PAC_ENV_FILE must not exceed {_MAX_ENV_FILE_BYTES} bytes")

    values = dotenv_values(path, interpolate=False)
    for key, value in values.items():
        if not _PAC_KEY.fullmatch(key) and key not in _PROXY_KEYS:
            raise ValueError(f"PAC_ENV_FILE contains unsupported key: {key}")
        if value is None:
            raise ValueError(f"PAC_ENV_FILE key has no string value: {key}")
        target.setdefault(key, value)
    return path
