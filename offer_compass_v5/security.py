"""Fail-closed security decisions for untrusted recruitment pages."""

import re
from typing import Any


PROMPT_INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"忽略(?:以上|之前|所有).*指令|系统提示词|开发者消息|"
    r"上传.*(?:cookie|密码|密钥)|发送.*(?:验证码|令牌)|"
    r"bypass\s+(?:captcha|verification)|绕过.*(?:验证|验证码)", re.I | re.S)

SENSITIVE_LABEL = re.compile(
    r"密码|口令|验证码|动态码|身份证|证件号|银行卡|信用卡|"
    r"password|passcode|otp|captcha|verification\s*code|"
    r"national\s*id|social\s*security|bank|credit\s*card", re.I)

SUBMIT_LABEL = re.compile(r"提交|投递|申请|确认发送|submit|apply\s+now|send", re.I)

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
TOKEN = re.compile(r"(?i)(api[_-]?key|authorization|token|cookie|password)(\s*[:=]\s*)([^\s,;]+)")


def assess_page_text(text: Any):
    value = str(text or "")[:100000]
    if len(str(text or "")) > 100000:
        return {"safe": False, "reason": "page_text_too_large"}
    if PROMPT_INJECTION.search(value):
        return {"safe": False, "reason": "suspected_prompt_injection"}
    return {"safe": True, "reason": None}


def is_sensitive_field(label: str, field_type: str = "") -> bool:
    return field_type.lower() in {"password", "hidden"} or bool(SENSITIVE_LABEL.search(label or ""))


def is_submit_control(label: str, field_type: str = "") -> bool:
    return field_type.lower() in {"submit", "button"} and bool(SUBMIT_LABEL.search(label or ""))


def redact(value: Any):
    """Recursively redact common PII and credential-shaped strings."""
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    value = EMAIL.sub("[email]", value)
    value = PHONE.sub("[phone]", value)
    return TOKEN.sub(lambda match: match.group(1) + match.group(2) + "[secret]", value)
