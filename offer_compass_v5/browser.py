"""Human-in-the-loop form-fill planning. This module never submits a form."""

import json
import sqlite3
import uuid
from contextlib import closing
from urllib.parse import urlparse

from .schemas import BrowserField, ValidationError, bounded_dict, clean_text
from .security import assess_page_text, is_sensitive_field, is_submit_control, redact
from .store import now


ALIASES = {
    "name": ("姓名", "名字", "name", "full name"),
    "email": ("邮箱", "电子邮件", "email", "e-mail"),
    "phone": ("手机", "电话", "联系电话", "phone", "mobile"),
    "city": ("城市", "所在地", "city", "location"),
    "school": ("学校", "院校", "university", "school"),
    "major": ("专业", "major"),
    "degree": ("学历", "学位", "degree", "education"),
    "linkedin": ("linkedin",),
    "github": ("github",),
}


def _candidate_key(field):
    label = (field.label + " " + field.name).casefold()
    for key, aliases in ALIASES.items():
        if any(alias.casefold() in label for alias in aliases):
            return key
    return None


def plan(db_path, body):
    if not isinstance(body, dict):
        raise ValidationError("invalid_request")
    page_url = clean_text(body.get("page_url"), 1200, True)
    parsed = urlparse(page_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValidationError("https_page_required")
    page_check = assess_page_text(body.get("page_text", ""))
    raw_fields = body.get("fields")
    if not isinstance(raw_fields, list) or not 0 < len(raw_fields) <= 200:
        raise ValidationError("invalid_fields")
    candidate = bounded_dict(body.get("candidate") or {})
    mappings, skipped = [], []
    if page_check["safe"]:
        for index, raw in enumerate(raw_fields):
            field = BrowserField.from_dict(raw, index)
            label = field.label or field.name
            if is_sensitive_field(label, field.field_type):
                skipped.append({"field_id": field.field_id, "reason": "sensitive_or_verification_field"})
                continue
            if is_submit_control(label, field.field_type):
                skipped.append({"field_id": field.field_id, "reason": "final_submit_requires_user"})
                continue
            key = _candidate_key(field)
            if key and candidate.get(key):
                mappings.append({"field_id": field.field_id, "label": label, "candidate_key": key,
                                 "value": candidate[key], "action": "fill_after_user_review"})
            else:
                skipped.append({"field_id": field.field_id, "reason": "no_reliable_mapping"})
    plan_id = "fill_" + uuid.uuid4().hex
    result = {"plan_id": plan_id, "status": "blocked" if not page_check["safe"] else "awaiting_user_review",
              "page_url": page_url, "page_assessment": page_check, "mappings": mappings,
              "skipped": skipped, "will_submit": False, "requires_user_confirmation": True,
              "message": "已生成安全填写计划；登录、验证码、不确定字段和最终提交由用户完成。"}
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("INSERT INTO browser_assist_runs VALUES (?, ?, ?, ?, ?)",
                   (plan_id, page_url, result["status"], json.dumps(redact(result), ensure_ascii=False), now()))
    return result
