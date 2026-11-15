"""V5 service facade used by the compatibility HTTP server."""

import os

from . import VERSION
from .applications import list_packages, save_package
from .browser import plan as browser_plan
from .jobs import discover, list_jobs
from .orchestrator import ApplicationGraph
from .scoring import deep_score, local_screen
from .store import rows


def capabilities():
    return {
        "version": VERSION,
        "positioning": "evidence_grounded_job_application_agent",
        "capabilities": ["durable_agent_graph", "trusted_job_discovery", "local_job_screening",
                         "budgeted_cached_model_scoring", "evidence_fit_assessment",
                         "verified_resume_patches", "application_packages", "human_reviewed_form_fill",
                         "security_evals"],
        "boundaries": {"final_submit": "user_only", "captcha": "user_only", "password": "never_stored",
                       "page_text": "untrusted_evidence", "model_scoring": "explicit_consent_only"},
        "model_scoring": {"configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                          "default_model": os.environ.get("OFFER_COMPASS_MODEL", "gpt-5.4-mini")},
    }


def list_tasks(db_path, limit=30):
    limit = max(1, min(int(limit), 100))
    return {"tasks": rows(db_path, """SELECT task_id, job_id, agent_run_id, stage, status,
        created_at, updated_at FROM v5_tasks ORDER BY updated_at DESC LIMIT ?""", (limit,))}


__all__ = ["capabilities", "discover", "list_jobs", "save_package", "list_packages",
           "browser_plan", "ApplicationGraph", "list_tasks", "local_screen", "deep_score"]
