"""Application package persistence and monotonic status history."""

import json
import sqlite3
import uuid
from contextlib import closing

from .schemas import ValidationError, clean_text
from .store import now, rows


STATUSES = ["intent", "preparing", "ready", "applied", "assessment", "interview", "offer", "closed"]


def save_package(db_path, body):
    if not isinstance(body, dict):
        raise ValidationError("invalid_package")
    package_id = clean_text(body.get("package_id"), 80) or "pkg_" + uuid.uuid4().hex
    status = clean_text(body.get("status", "intent"), 30, True)
    if status not in STATUSES:
        raise ValidationError("invalid_status")
    company = clean_text(body.get("company"), 200, True)
    role = clean_text(body.get("role"), 300, True)
    job_id = clean_text(body.get("job_id"), 80) or None
    run_id = clean_text(body.get("agent_run_id"), 80) or None
    official_url = clean_text(body.get("official_url"), 1200)
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValidationError("invalid_payload")
    stamp = now()
    with closing(sqlite3.connect(str(db_path))) as db, db:
        previous = db.execute("SELECT status, created_at FROM application_packages WHERE package_id=?", (package_id,)).fetchone()
        created = previous[1] if previous else stamp
        db.execute("""INSERT INTO application_packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id) DO UPDATE SET job_id=excluded.job_id,
              agent_run_id=excluded.agent_run_id, company=excluded.company, role=excluded.role,
              official_url=excluded.official_url, status=excluded.status,
              payload_json=excluded.payload_json, updated_at=excluded.updated_at""", (
            package_id, job_id, run_id, company, role, official_url, status,
            json.dumps(payload, ensure_ascii=False), created, stamp))
        if not previous or previous[0] != status:
            db.execute("INSERT INTO application_events (package_id, from_status, to_status, evidence, created_at) VALUES (?, ?, ?, ?, ?)",
                       (package_id, previous[0] if previous else None, status,
                        clean_text(body.get("evidence"), 1000), stamp))
    return get_package(db_path, package_id)


def get_package(db_path, package_id):
    found = rows(db_path, "SELECT * FROM application_packages WHERE package_id=?", (package_id,))
    if not found:
        raise KeyError(package_id)
    item = found[0]
    item["payload"] = json.loads(item.pop("payload_json"))
    item["events"] = rows(db_path, "SELECT from_status, to_status, evidence, created_at FROM application_events WHERE package_id=? ORDER BY event_id", (package_id,))
    return item


def list_packages(db_path, limit=50):
    limit = max(1, min(int(limit), 200))
    items = rows(db_path, "SELECT * FROM application_packages ORDER BY updated_at DESC LIMIT ?", (limit,))
    for item in items:
        item["payload"] = json.loads(item.pop("payload_json"))
    return {"applications": items}
