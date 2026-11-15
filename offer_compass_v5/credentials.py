"""Windows user-bound encrypted storage for optional local model credentials."""

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


CRYPTPROTECT_UI_FORBIDDEN = 0x1
DESCRIPTION = "Offer Compass model credentials"


def _crypt(data, protect):
    if os.name != "nt":
        raise RuntimeError("windows_credential_store_required")
    source_buffer = ctypes.create_string_buffer(data)
    source = DATA_BLOB(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    result = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), DESCRIPTION, None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def save(path, config):
    allowed = {key: str(config.get(key) or "") for key in
               ("provider", "base_url", "model", "api_key")}
    if not all(allowed.values()):
        raise RuntimeError("model_not_configured")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _crypt(json.dumps(allowed, ensure_ascii=False).encode("utf-8"), True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(encrypted)
    os.replace(str(temporary), str(target))


def load(path):
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(_crypt(target.read_bytes(), False).decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    required = ("provider", "base_url", "model", "api_key")
    return payload if isinstance(payload, dict) and all(payload.get(key) for key in required) else None


def apply(path):
    config = load(path)
    if not config:
        return False
    os.environ["OFFER_COMPASS_MODEL_PROVIDER"] = config["provider"]
    os.environ["OFFER_COMPASS_MODEL_BASE_URL"] = config["base_url"]
    os.environ["OFFER_COMPASS_MODEL"] = config["model"]
    key_name = "DASHSCOPE_API_KEY" if config["provider"] == "aliyun" else "OPENAI_API_KEY"
    os.environ[key_name] = config["api_key"]
    return True
