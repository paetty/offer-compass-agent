"""V5 service facade used by the compatibility HTTP server."""

from . import VERSION
from .applications import list_packages, save_package
from .browser import plan as browser_plan
from .jobs import discover, list_jobs
from .orchestrator import ApplicationGraph
from .store import rows


def capabilities():
    return {
        "version": VERSION,
        "positioning": "evidence_grounded_job_application_agent",
        "capabilities": ["durable_agent_graph", "trusted_job_discovery", "evidence_fit_assessment",
                         "verified_resume_patches", "application_packages", "human_reviewed_form_fill",
                         "security_evals"],
        "boundaries": {"final_submit": "user_only", "captcha": "user_only", "password": "never_stored",
                       "page_text": "untrusted_evidence"},
    }


def list_tasks(db_path, limit=30):
    limit = max(1, min(int(limit), 100))
    return {"tasks": rows(db_path, """SELECT task_id, job_id, agent_run_id, stage, status,
        created_at, updated_at FROM v5_tasks ORDER BY updated_at DESC LIMIT ?""", (limit,))}


__all__ = ["capabilities", "discover", "list_jobs", "save_package", "list_packages",
           "browser_plan", "ApplicationGraph", "list_tasks"]
