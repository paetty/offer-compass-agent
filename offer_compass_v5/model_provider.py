"""Validated OpenAI-compatible model provider configuration for local sessions."""

import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .schemas import ValidationError


PROVIDERS = {
    "openai": {"key_env": "OPENAI_API_KEY", "default_base": "https://api.openai.com/v1",
               "default_model": "gpt-5.4-mini"},
    "aliyun": {"key_env": "DASHSCOPE_API_KEY", "default_base": "",
               "default_model": "qwen3.8-flash"},
}


def _provider():
    provider = os.environ.get("OFFER_COMPASS_MODEL_PROVIDER", "openai").strip().lower()
    if provider not in PROVIDERS:
        raise ValidationError("unsupported_model_provider")
    return provider


def _valid_base(provider, value):
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if provider == "openai":
        return host == "api.openai.com" and path == "/v1"
    return ((host.endswith(".cn-beijing.maas.aliyuncs.com") and path.endswith("/compatible-mode/v1"))
            or (host == "dashscope.aliyuncs.com" and path.endswith("/compatible-mode/v1")))


def settings(require_configured=False):
    provider = _provider()
    spec = PROVIDERS[provider]
    base_url = os.environ.get("OFFER_COMPASS_MODEL_BASE_URL", "").strip() or spec["default_base"]
    model = os.environ.get("OFFER_COMPASS_MODEL", "").strip() or spec["default_model"]
    key = os.environ.get(spec["key_env"], "").strip()
    valid_base = bool(base_url and _valid_base(provider, base_url))
    configured = bool(key and valid_base and model)
    if require_configured and not configured:
        raise RuntimeError("model_not_configured")
    return {"provider": provider, "model": model, "base_url": base_url,
            "base_host": urlparse(base_url).hostname or "", "key_env": spec["key_env"],
            "configured": configured, "base_url_valid": valid_base, "api_key": key}


def public_settings():
    config = settings()
    return {key: config[key] for key in
            ("provider", "model", "base_host", "configured", "base_url_valid", "key_env")}


def responses(payload, timeout=30):
    config = settings(require_configured=True)
    request = Request(config["base_url"].rstrip("/") + "/responses",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": "Bearer " + config["api_key"],
                               "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def test_connection():
    config = settings(require_configured=True)
    result = responses({"model": config["model"], "input": "只回复 OK",
                        "max_output_tokens": 16, "store": False}, timeout=20)
    usage = result.get("usage") or {}
    return {"ok": result.get("status") in {None, "completed"},
            "provider": config["provider"], "model": config["model"],
            "usage": {"input_tokens": int(usage.get("input_tokens") or 0),
                      "output_tokens": int(usage.get("output_tokens") or 0),
                      "total_tokens": int(usage.get("total_tokens") or 0)}}
