"""Persistent single-agent state machine and audited tool runner for Offer Compass."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone

from offer_compass_tools import TOOL_REGISTRY


STAGES = {"jd_received", "evidence_retrieval", "clarification_required", "fit_assessed",
          "rewrite_generated", "verification_failed", "ready_for_review"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize(db_path):
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                input_hash TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL,
                output_json TEXT,
                error TEXT,
                duration_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
            )
        """)
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS agent_tool_sequence ON agent_tool_calls(run_id, sequence)")


def _validate(body):
    if not isinstance(body, dict):
        raise ValueError("invalid request")
    jd, lines, levels = body.get("jd", ""), body.get("lines", []), body.get("levels", {})
    if not (isinstance(jd, str) and 0 < len(jd) <= 24000
            and isinstance(lines, list) and 0 < len(lines) <= 1000
            and all(isinstance(line, str) and len(line) <= 4000 for line in lines)
            and sum(map(len, lines)) <= 60000
            and isinstance(levels, dict) and len(levels) <= 200
            and all(isinstance(key, str) and type(value) is int and 0 <= value <= 3
                    for key, value in levels.items())):
        raise ValueError("invalid request")
    for key, maximum in (("goal", 1000), ("answer", 1200), ("run_id", 80)):
        if not isinstance(body.get(key, ""), str) or len(body.get(key, "")) > maximum:
            raise ValueError("invalid request")
    if "confirmed" in body and type(body["confirmed"]) is not bool:
        raise ValueError("invalid request")


def _input_hash(jd, lines):
    value = json.dumps({"jd": jd, "lines": lines}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AgentRuntime:
    def __init__(self, db_path):
        self.db_path = db_path

    def _load(self, run_id):
        with closing(sqlite3.connect(str(self.db_path))) as db:
            row = db.execute("SELECT state_json FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, state, new=False):
        stamp = _now()
        state["updated_at"] = stamp
        with closing(sqlite3.connect(str(self.db_path))) as db, db:
            if new:
                state["created_at"] = stamp
                db.execute("INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                    state["run_id"], state["stage"], state["status"], state["revision"],
                    state["input_hash"], json.dumps(state, ensure_ascii=False), stamp, stamp))
            else:
                db.execute("""UPDATE agent_runs SET stage = ?, status = ?, revision = ?,
                           state_json = ?, updated_at = ? WHERE run_id = ?""", (
                    state["stage"], state["status"], state["revision"],
                    json.dumps(state, ensure_ascii=False), stamp, state["run_id"]))

    def _tool(self, state, name, arguments):
        if name not in TOOL_REGISTRY:
            raise ValueError("unknown tool")
        sequence = len(state.get("tool_trace", [])) + 1
        started = time.perf_counter()
        status, result, error = "completed", None, None
        try:
            result = TOOL_REGISTRY[name](**arguments)
            return result
        except Exception as exc:
            status, error = "failed", str(exc)[:500]
            raise
        finally:
            duration = max(0, int((time.perf_counter() - started) * 1000))
            summary = self._summary(name, result)
            trace = {"sequence": sequence, "tool": name, "status": status,
                     "duration_ms": duration, "summary": summary}
            state.setdefault("tool_trace", []).append(trace)
            with closing(sqlite3.connect(str(self.db_path))) as db, db:
                db.execute("""INSERT INTO agent_tool_calls
                    (run_id, sequence, tool_name, status, input_json, output_json, error, duration_ms, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    state["run_id"], sequence, name, status,
                    json.dumps(arguments, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error, duration, _now()))

    @staticmethod
    def _summary(name, result):
        if not isinstance(result, dict):
            return "已完成"
        labels = {
            "get_candidate_profile": "已读取 {} 行档案".format(result.get("line_count", 0)),
            "parse_job_description": "已识别 {} 项岗位要求".format(result.get("requirement_count", 0)),
            "retrieve_resume_evidence": "已找到 {} 项匹配证据".format(result.get("matched_count", 0)),
            "assess_job_fit": "已生成投递判断：{}".format(result.get("verdict", "conditional")),
            "create_clarifying_question": "已生成 1 个关键追问",
            "record_candidate_clarification": "已记录用户回答（{}）".format(
                "已确认" if result.get("confirmed") else "未作为证据"),
            "generate_resume_patches": "已生成 {} 处结构化修改".format(result.get("patch_count", 0)),
            "verify_resume_patches": "修改校验：{} 处通过，{} 处阻止".format(
                result.get("accepted_count", 0), result.get("rejected_count", 0)),
        }
        return labels.get(name, "已完成")

    def run(self, body):
        _validate(body)
        run_id = body.get("run_id", "").strip()
        state = self._load(run_id) if run_id else None
        digest = _input_hash(body["jd"], body["lines"])
        if state and state["input_hash"] != digest:
            raise ValueError("run input changed")
        if not state:
            run_id = "run_" + uuid.uuid4().hex
            state = {"run_id": run_id, "stage": "jd_received", "status": "running",
                     "revision": 0, "input_hash": digest, "goal": body.get("goal", ""),
                     "jd": body["jd"], "lines": body["lines"], "levels": body["levels"],
                     "tool_trace": [], "clarification": None}
            self._save(state, new=True)
        state["revision"] += 1
        state["stage"], state["status"] = "evidence_retrieval", "running"
        profile = self._tool(state, "get_candidate_profile", {"lines": state["lines"], "levels": state["levels"]})
        parsed = self._tool(state, "parse_job_description", {"jd": state["jd"]})
        evidence = self._tool(state, "retrieve_resume_evidence", {
            "requirements": parsed["requirements"], "profile": profile})
        clarification = state.get("clarification")
        waiting = state.get("question")
        answer = body.get("answer", "").strip()
        if waiting and (answer or body.get("skip") is True):
            clarification = self._tool(state, "record_candidate_clarification", {
                "question": waiting, "answer": answer if answer else "",
                "confirmed": body.get("confirmed") is True, "skipped": body.get("skip") is True})
            state["clarification"] = clarification
            state.pop("question", None)
        assessment = self._tool(state, "assess_job_fit", {
            "requirements": parsed["requirements"], "evidence": evidence,
            "clarification": clarification if clarification and not clarification.get("skipped") else None})
        if assessment["critical_gap"] and not clarification:
            question = self._tool(state, "create_clarifying_question", {"gap": assessment["critical_gap"]})
            state.update({"stage": "clarification_required", "status": "waiting", "question": question,
                          "assessment": assessment})
        else:
            state.update({"stage": "fit_assessed", "status": "running", "assessment": assessment})
            state.pop("question", None)
            patch_result = {"patches": [], "patch_count": 0}
            verification = {"accepted": [], "rejected": [], "accepted_count": 0, "rejected_count": 0}
            if assessment["verdict"] != "hold" and assessment["strong_count"]:
                state["stage"] = "rewrite_generated"
                patch_result = self._tool(state, "generate_resume_patches", {
                    "jd": state["jd"], "requirements": parsed["requirements"],
                    "assessment": assessment, "profile": profile})
                verification = self._tool(state, "verify_resume_patches", {
                    "patches": patch_result["patches"], "profile": profile,
                    "clarification": clarification})
            if verification["accepted"]:
                state["stage"] = "ready_for_review"
            elif verification["rejected"]:
                state["stage"] = "verification_failed"
            else:
                state["stage"] = "fit_assessed"
            state["status"] = "completed"
            state["patches"] = verification["accepted"]
            state["rejected_patches"] = verification["rejected"]
        self._save(state)
        return self.public(state)

    def get(self, run_id):
        state = self._load(run_id)
        if not state:
            raise KeyError(run_id)
        return self.public(state)

    def list_runs(self, limit=20):
        limit = max(1, min(int(limit), 50))
        with closing(sqlite3.connect(str(self.db_path))) as db:
            rows = db.execute("""SELECT run_id, stage, status, revision, state_json, created_at, updated_at
                               FROM agent_runs ORDER BY updated_at DESC LIMIT ?""", (limit,)).fetchall()
        items = []
        for run_id, stage, status, revision, raw, created_at, updated_at in rows:
            state = json.loads(raw)
            assessment = state.get("assessment", {})
            items.append({"run_id": run_id, "stage": stage, "status": status, "revision": revision,
                          "goal": str(state.get("goal", ""))[:120],
                          "jd_excerpt": str(state.get("jd", "")).replace("\n", " ")[:100],
                          "verdict": assessment.get("verdict"),
                          "evidence_count": sum(bool(item.get("evidence_id")) for item in assessment.get("requirements", [])),
                          "gap_count": len(assessment.get("gaps", [])),
                          "patch_count": len(state.get("patches", [])),
                          "tool_count": len(state.get("tool_trace", [])),
                          "created_at": created_at, "updated_at": updated_at})
        return {"agent_version": "4.5", "runs": items}

    @staticmethod
    def public(state):
        assessment = state.get("assessment", {})
        evidence = [{"evidence_id": item.get("evidence_id"), "line": item.get("line"),
                     "text": item.get("text") or item.get("answer"),
                     "requirement_id": item["requirement_id"], "requirement": item["requirement"],
                     "status": item["status"], "level": item.get("evidence_level"),
                     "source": item.get("source")}
                    for item in assessment.get("requirements", [])
                    if item.get("line") or item.get("source") == "candidate_confirmation"]
        gaps = [item["requirement"] for item in assessment.get("gaps", [])]
        verdict = assessment.get("verdict")
        summary = {
            "apply": "主要岗位要求已有简历经历或用户确认作为依据。",
            "conditional": "已找到部分经历证据，仍有必备要求需要确认。",
            "hold": "当前可验证的经历证据不足，建议先补充关键信息。",
        }.get(verdict, "正在核对档案证据。")
        question = state.get("question")
        next_actions = {"clarification_required": "wait_for_user", "ready_for_review": "review_resume_patches",
                        "verification_failed": "inspect_verification_failure", "fit_assessed": "review_fit_assessment"}
        return {"run_id": state["run_id"], "agent_version": "4.5",
                "stage": state["stage"], "status": state["status"],
                "revision": state["revision"], "verdict": verdict, "summary": summary,
                "evidence": evidence[:10], "gaps": gaps[:10],
                "question": question["question"] if question else None,
                "requirement": question["requirement"] if question else None,
                "requirement_id": question["requirement_id"] if question else None,
                "patches": state.get("patches", []),
                "rejected_patches": [{"patch_id": item.get("patch_id"),
                                      "reasons": item.get("verification_reasons", [])}
                                     for item in state.get("rejected_patches", [])],
                "clarification": state.get("clarification"),
                "created_at": state.get("created_at"), "updated_at": state.get("updated_at"),
                "next_action": next_actions.get(state["stage"], "review_fit_assessment"),
                "tool_trace": state.get("tool_trace", [])}

