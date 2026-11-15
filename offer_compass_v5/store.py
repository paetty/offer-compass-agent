"""Ordered SQLite migrations and repositories for V5 data."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


MIGRATIONS = [(1, """
CREATE TABLE IF NOT EXISTS job_sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    locator TEXT NOT NULL,
    company TEXT NOT NULL,
    industry TEXT NOT NULL,
    last_status TEXT NOT NULL,
    last_error TEXT,
    checked_at TEXT NOT NULL,
    UNIQUE(source_type, locator)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    industry TEXT NOT NULL,
    city TEXT NOT NULL,
    jd TEXT NOT NULL,
    official_url TEXT NOT NULL,
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source_id, external_id),
    FOREIGN KEY(source_id) REFERENCES job_sources(source_id)
);
CREATE TABLE IF NOT EXISTS application_packages (
    package_id TEXT PRIMARY KEY,
    job_id TEXT,
    agent_run_id TEXT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    official_url TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
CREATE TABLE IF NOT EXISTS application_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(package_id) REFERENCES application_packages(package_id)
);
CREATE TABLE IF NOT EXISTS v5_tasks (
    task_id TEXT PRIMARY KEY,
    job_id TEXT,
    agent_run_id TEXT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS browser_assist_runs (
    plan_id TEXT PRIMARY KEY,
    page_url TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
""")]


def initialize(db_path):
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""")
        applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            db.executescript(sql)
            db.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, now()))


def rows(db_path, query, params=()):
    with closing(sqlite3.connect(str(db_path))) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, params).fetchall()]


def save_task(db_path, state):
    stamp = now()
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("""INSERT INTO v5_tasks
            (task_id, job_id, agent_run_id, stage, status, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET agent_run_id=excluded.agent_run_id,
              stage=excluded.stage, status=excluded.status, state_json=excluded.state_json,
              updated_at=excluded.updated_at""", (
            state["task_id"], state.get("job_id"), state.get("agent_run_id"), state["stage"],
            state["status"], json.dumps(state, ensure_ascii=False),
            state.get("created_at", stamp), stamp))
