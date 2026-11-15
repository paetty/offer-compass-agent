"""Serve the local Offer Compass workspace and store it in SQLite."""

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import webbrowser
from contextlib import closing
from getpass import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import offer_compass_agent
import offer_compass_runtime
from offer_compass_v5 import VERSION as AGENT_VERSION
from offer_compass_v5 import api as v5_api
from offer_compass_v5 import credentials as model_credentials
from offer_compass_v5 import model_provider
from offer_compass_v5 import store as v5_store
from offer_compass_v5.schemas import ValidationError


ROOT = Path(__file__).resolve().parent
MAX_BODY = 32 * 1024 * 1024
WORD_PREVIEW_MAX = 12 * 1024 * 1024
WORD_AUTOMATION_LOCK = threading.Lock()


def word_pdf_preview(source):
    """Render a DOCX with desktop Word so floating images and wrapping stay faithful."""
    with WORD_AUTOMATION_LOCK, tempfile.TemporaryDirectory(prefix="offer-word-preview-") as folder:
        docx_path = Path(folder) / "resume.docx"
        pdf_path = Path(folder) / "resume.pdf"
        docx_path.write_bytes(source)
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                [sys.executable, str(ROOT / "word_preview_worker.py"), str(docx_path), str(pdf_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=True, timeout=7, creationflags=flags
            )
        except (subprocess.SubprocessError, OSError) as error:
            raise RuntimeError("Word preview unavailable") from error
        return pdf_path.read_bytes()


def initialize(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS workspace (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                revision INTEGER NOT NULL,
                change_id TEXT,
                data TEXT
            )
        """)
        db.execute("INSERT OR IGNORE INTO workspace VALUES (1, 0, NULL, NULL)")
    offer_compass_runtime.initialize(db_path)
    v5_store.initialize(db_path)


def valid_update(body):
    if not isinstance(body, dict):
        return False
    data = body.get("data")
    return (
        type(body.get("revision")) is int and body["revision"] >= 0
        and isinstance(body.get("id"), str) and 0 < len(body["id"]) <= 80
        and isinstance(data, dict) and isinstance(data.get("profile"), dict)
        and isinstance(data.get("applications"), list) and isinstance(data.get("weights"), dict)
    )


class WorkspaceServer(ThreadingHTTPServer):
    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class WorkspaceHandler(BaseHTTPRequestHandler):
    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        self.send_body(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    def expected_host(self):
        return "127.0.0.1:{}".format(self.server.server_port)

    def do_GET(self):
        if self.headers.get("Host") != self.expected_host():
            self.send_error(403)
            return
        route = urlparse(self.path)
        if route.path == "/":
            self.send_body(200, (ROOT / "Offer Compass.html").read_bytes(), "text/html; charset=utf-8")
        elif route.path == "/api/health":
            self.send_json(200, {"app": "Offer Compass", "agent_version": AGENT_VERSION})
        elif route.path == "/api/agent":
            self.send_json(200, {"configured": offer_compass_agent.configured(), "version": AGENT_VERSION})
        elif route.path == "/api/v5/capabilities":
            self.send_json(200, v5_api.capabilities())
        elif route.path == "/api/v5/jobs":
            try:
                limit = int(parse_qs(route.query).get("limit", ["50"])[0])
                self.send_json(200, v5_api.list_jobs(self.server.db_path, limit))
            except (ValueError, sqlite3.Error):
                self.send_json(500, {"error": "job_catalog_unavailable"})
        elif route.path == "/api/v5/applications":
            try:
                limit = int(parse_qs(route.query).get("limit", ["50"])[0])
                self.send_json(200, v5_api.list_packages(self.server.db_path, limit))
            except (ValueError, sqlite3.Error):
                self.send_json(500, {"error": "application_catalog_unavailable"})
        elif route.path == "/api/v5/tasks":
            try:
                limit = int(parse_qs(route.query).get("limit", ["30"])[0])
                self.send_json(200, v5_api.list_tasks(self.server.db_path, limit))
            except (ValueError, sqlite3.Error):
                self.send_json(500, {"error": "task_catalog_unavailable"})
        elif route.path == "/api/agent/runs":
            try:
                limit = int(parse_qs(route.query).get("limit", ["20"])[0])
                result = offer_compass_runtime.AgentRuntime(self.server.db_path).list_runs(limit)
            except (ValueError, sqlite3.Error):
                self.send_json(500, {"error": "agent_history_unavailable"})
            else:
                self.send_json(200, result)
        elif route.path == "/api/agent/run":
            run_id = parse_qs(route.query).get("run_id", [""])[0]
            if not run_id or len(run_id) > 80:
                self.send_json(400, {"error": "invalid_run_id"})
                return
            try:
                result = offer_compass_runtime.AgentRuntime(self.server.db_path).get(run_id)
            except KeyError:
                self.send_json(404, {"error": "run_not_found"})
            except (ValueError, sqlite3.Error):
                self.send_json(500, {"error": "agent_state_unavailable"})
            else:
                self.send_json(200, result)
        elif route.path == "/api/workspace":
            try:
                with closing(sqlite3.connect(str(self.server.db_path))) as db:
                    revision, change_id, data = db.execute(
                        "SELECT revision, change_id, data FROM workspace WHERE id = 1"
                    ).fetchone()
            except sqlite3.Error:
                self.send_json(500, {"error": "database"})
                return
            self.send_json(200, {"revision": revision, "id": change_id, "data": json.loads(data) if data else None})
        else:
            self.send_error(404)

    def do_POST(self):
        host = self.expected_host()
        if self.headers.get("Host") != host or self.headers.get("Origin") != "http://" + host:
            self.send_error(403)
            return
        if self.path.startswith("/api/v5/"):
            if self.headers.get_content_type() != "application/json":
                self.send_error(415)
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 2 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if self.path == "/api/v5/jobs/discover":
                    result = v5_api.discover(self.server.db_path, body)
                elif self.path == "/api/v5/jobs/screen":
                    result = v5_api.local_screen(self.server.db_path, body)
                elif self.path == "/api/v5/jobs/deep-score":
                    result = v5_api.deep_score(self.server.db_path, body)
                elif self.path == "/api/v5/model/test":
                    if body.get("allow_model") is not True:
                        raise ValidationError("model_consent_required")
                    result = v5_api.test_connection()
                elif self.path == "/api/v5/agent/run":
                    result = v5_api.ApplicationGraph(self.server.db_path).run(body)
                elif self.path == "/api/v5/applications":
                    result = v5_api.save_package(self.server.db_path, body)
                elif self.path == "/api/v5/browser/plan":
                    result = v5_api.browser_plan(self.server.db_path, body)
                else:
                    self.send_error(404)
                    return
            except ValidationError as error:
                self.send_json(400, {"error": str(error) or "invalid_v5_input"})
            except (ValueError, RecursionError):
                self.send_json(400, {"error": "invalid_v5_input"})
            except KeyError:
                self.send_json(404, {"error": "v5_record_not_found"})
            except (URLError, TimeoutError, OSError) as error:
                reason = getattr(error, "code", None)
                self.send_json(502, {"error": "model_scoring_unavailable" if self.path.endswith("/deep-score")
                                     else "job_source_unavailable",
                                     "reason": "provider_http_{}".format(reason) if reason else "provider_network_error"})
            except RuntimeError as error:
                if str(error) == "model_not_configured":
                    self.send_json(503, {"error": "model_not_configured"})
                else:
                    self.send_json(502, {"error": "model_scoring_unavailable", "reason": str(error)[:120]})
            except sqlite3.Error:
                self.send_json(500, {"error": "v5_database_unavailable"})
            except Exception:
                self.send_json(500, {"error": "v5_operation_failed"})
            else:
                self.send_json(200, result)
            return
        if self.path == "/api/word-preview":
            if self.headers.get_content_type() != (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
                self.send_error(415)
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= WORD_PREVIEW_MAX:
                    self.send_error(413)
                    return
                result = word_pdf_preview(self.rfile.read(size))
                if not result.startswith(b"%PDF-"):
                    raise RuntimeError("Word did not create a PDF")
            except (ImportError, OSError, RuntimeError, ValueError):
                self.send_json(503, {"error": "word_preview_unavailable"})
            except Exception:
                self.send_json(503, {"error": "word_preview_unavailable"})
            else:
                self.send_body(200, result, "application/pdf")
            return
        if self.path == "/api/agent/run":
            if self.headers.get_content_type() != "application/json":
                self.send_error(415)
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 512 * 1024:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                result = offer_compass_runtime.AgentRuntime(self.server.db_path).run(body)
            except (ValueError, RecursionError):
                self.send_json(400, {"error": "invalid_agent_input"})
            except sqlite3.Error:
                self.send_json(500, {"error": "agent_state_unavailable"})
            except Exception:
                self.send_json(500, {"error": "agent_runtime_failed"})
            else:
                self.send_json(200, result)
            return
        if self.path != "/api/agent":
            self.send_error(404)
            return
        if not offer_compass_agent.configured():
            self.send_json(503, {"error": "model_not_configured"})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 512 * 1024:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(body, dict) or body.get("allowModel") is not True:
                self.send_json(400, {"error": "model_consent_required"})
                return
            result = offer_compass_agent.prepare(body)
        except (ValueError, RecursionError):
            self.send_json(400, {"error": "invalid_agent_input"})
        except (URLError, TimeoutError, KeyError, TypeError, RuntimeError):
            self.send_json(502, {"error": "model_unavailable"})
        else:
            self.send_json(200, result)

    def do_PUT(self):
        host = self.expected_host()
        if self.headers.get("Host") != host or self.headers.get("Origin") != "http://" + host:
            self.send_error(403)
            return
        if self.path != "/api/workspace":
            self.send_error(404)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= MAX_BODY:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            if not valid_update(body):
                raise ValueError("Invalid workspace")
            data = json.dumps(body["data"], ensure_ascii=True, allow_nan=False)
        except (ValueError, RecursionError):
            self.send_json(400, {"error": "invalid_workspace"})
            return
        try:
            with closing(sqlite3.connect(str(self.server.db_path))) as db, db:
                db.execute("BEGIN IMMEDIATE")
                revision, change_id = db.execute(
                    "SELECT revision, change_id FROM workspace WHERE id = 1"
                ).fetchone()
                if body["id"] == change_id:
                    status = 200
                elif body["revision"] != revision and not (change_id and body.get("after") == change_id):
                    status = 409
                else:
                    revision += 1
                    db.execute("UPDATE workspace SET revision = ?, change_id = ?, data = ? WHERE id = 1",
                               (revision, body["id"], data))
                    status = 200
        except sqlite3.Error:
            self.send_json(500, {"error": "database"})
            return
        self.send_json(status, {"revision": revision})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "offer-compass.sqlite3")
    parser.add_argument("--open", action="store_true", help="Open the workspace in the default browser")
    parser.add_argument("--ask-model-key", action="store_true", help="Read the selected provider API key for this session")
    parser.add_argument("--model-provider", choices=("openai", "aliyun"), help="OpenAI-compatible provider")
    parser.add_argument("--model-base-url", help="Validated provider API base URL; never includes the key")
    parser.add_argument("--model-name", help="Provider model ID")
    parser.add_argument("--use-saved-model-config", action="store_true",
                        help="Load the current Windows user's encrypted model configuration")
    parser.add_argument("--remember-model-config", action="store_true",
                        help="Encrypt the configured provider settings for the current Windows user")
    parser.add_argument("--configure-model-only", action="store_true",
                        help="Validate and save model settings, then exit without starting the site")
    args = parser.parse_args()
    credential_path = ROOT / "data" / "model-credentials.bin"
    if args.use_saved_model_config:
        model_credentials.apply(credential_path)
    if args.model_provider:
        os.environ["OFFER_COMPASS_MODEL_PROVIDER"] = args.model_provider
    if args.model_base_url:
        os.environ["OFFER_COMPASS_MODEL_BASE_URL"] = args.model_base_url
    if args.model_name:
        os.environ["OFFER_COMPASS_MODEL"] = args.model_name
    if args.ask_model_key and not model_provider.settings()["configured"]:
        provider = os.environ.get("OFFER_COMPASS_MODEL_PROVIDER", "openai").strip().lower()
        if provider == "aliyun" and not os.environ.get("OFFER_COMPASS_MODEL_BASE_URL", "").strip():
            base = input("Alibaba Model Studio Base URL (with WorkspaceId): ").strip()
            if base:
                os.environ["OFFER_COMPASS_MODEL_BASE_URL"] = base
        key = getpass("{} API key: ".format(
            "Alibaba Model Studio" if provider == "aliyun" else "OpenAI")).strip()
        if key:
            os.environ["DASHSCOPE_API_KEY" if provider == "aliyun" else "OPENAI_API_KEY"] = key
    config = model_provider.settings(require_configured=args.configure_model_only or args.remember_model_config)
    if args.remember_model_config:
        model_credentials.save(credential_path, config)
    if args.configure_model_only:
        result = model_provider.test_connection()
        if not result.get("ok"):
            raise SystemExit("Model connection test did not complete.")
        print("Model configuration encrypted for this Windows user: {} · {}".format(
            config["provider"], config["model"]), flush=True)
        return
    url = "http://127.0.0.1:{}/".format(args.port)
    try:
        server = WorkspaceServer(("127.0.0.1", args.port), WorkspaceHandler)
    except OSError:
        if args.open and not args.ask_model_key:
            try:
                with urlopen(url + "api/health", timeout=2) as response:
                    running = json.load(response).get("app") == "Offer Compass"
            except (URLError, ValueError, OSError):
                running = False
            if running:
                webbrowser.open(url)
                return
        raise SystemExit("Could not start Offer Compass on {}. The port may be in use.".format(url))
    with server:
        initialize(args.db)
        server.db_path = args.db
        url = "http://127.0.0.1:{}/".format(server.server_port)
        print("Offer Compass: {}".format(url), flush=True)
        print("Database: {}".format(args.db), flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
