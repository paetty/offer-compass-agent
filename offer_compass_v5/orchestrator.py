"""Durable explicit graph for job-to-application preparation.

The graph is intentionally framework-independent: its nodes are deterministic
Python services and the existing evidence Agent.  This keeps the V4.5 safety
contract while exposing the same pause/resume semantics expected from a graph
orchestrator.
"""

import hashlib
import uuid

import offer_compass_runtime

from . import VERSION
from .applications import save_package
from .jobs import get_job
from .schemas import ValidationError, clean_text
from .store import now, save_task


NODES = ("load_job", "evidence_agent", "human_clarification", "verified_draft", "application_package")


class ApplicationGraph:
    def __init__(self, db_path):
        self.db_path = db_path

    def run(self, body):
        if not isinstance(body, dict):
            raise ValidationError("invalid_request")
        task_id = clean_text(body.get("task_id"), 80) or "task_" + uuid.uuid4().hex
        job_id = clean_text(body.get("job_id"), 80, True)
        job = get_job(self.db_path, job_id)
        created = now()
        state = {"task_id": task_id, "job_id": job_id, "stage": "load_job", "status": "running",
                 "created_at": created, "agent_version": VERSION, "node_trace": ["load_job"]}
        save_task(self.db_path, state)
        agent_body = {
            "run_id": clean_text(body.get("agent_run_id"), 80), "jd": job["jd"],
            "goal": clean_text(body.get("goal"), 1000) or "判断岗位是否值得投；如果值得，准备可信的定制简历草稿。",
            "answer": clean_text(body.get("answer"), 1200), "confirmed": body.get("confirmed") is True,
            "skip": body.get("skip") is True, "lines": body.get("lines"), "levels": body.get("levels") or {},
        }
        state.update({"stage": "evidence_agent", "node_trace": state["node_trace"] + ["evidence_agent"]})
        save_task(self.db_path, state)
        result = offer_compass_runtime.AgentRuntime(self.db_path).run(agent_body)
        state["agent_run_id"] = result["run_id"]
        if result.get("question"):
            state.update({"stage": "human_clarification", "status": "waiting",
                          "node_trace": state["node_trace"] + ["human_clarification"]})
        elif result.get("patches"):
            state.update({"stage": "verified_draft", "status": "completed",
                          "node_trace": state["node_trace"] + ["verified_draft"]})
        else:
            state.update({"stage": "fit_assessed", "status": "completed"})
        state["result"] = result
        save_task(self.db_path, state)
        package = None
        if not result.get("question") and result.get("verdict") != "hold":
            package_id = "pkg_" + hashlib.sha256(
                (job_id + ":" + result["run_id"]).encode("utf-8")).hexdigest()[:24]
            package = save_package(self.db_path, {
                "package_id": package_id,
                "job_id": job_id, "agent_run_id": result["run_id"], "company": job["company"],
                "role": job["role"], "official_url": job["official_url"], "status": "preparing",
                "payload": {"verdict": result.get("verdict"), "evidence": result.get("evidence", []),
                            "gaps": result.get("gaps", []), "patches": result.get("patches", [])},
                "evidence": "Agent evidence assessment",
            })
            state.update({"stage": "application_package", "status": "completed",
                          "package_id": package["package_id"],
                          "node_trace": state["node_trace"] + ["application_package"]})
            save_task(self.db_path, state)
        return {"task_id": task_id, "job": {key: job[key] for key in
                ("job_id", "company", "role", "city", "official_url")},
                "stage": state["stage"], "status": state["status"], "node_trace": state["node_trace"],
                "agent": result, "application": package}
