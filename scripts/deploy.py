#!/usr/bin/env python3
"""Deploy the DE Pro quiz app with plain REST calls (fallback when the Databricks CLI / bundles
are not available). Idempotent: re-running updates code and skips what already exists.

Env: DATABRICKS_HOST, DATABRICKS_TOKEN
Usage: deploy.py [--sample]   (--sample also uploads samples/2026-11-07.json)
"""
import base64
import json
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dbx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP = "depro-quiz"
PIPELINE = "depro-quiz-pipeline"


def log(*a):
    print("[deploy]", *a, flush=True)


def me():
    return dbx.call("GET", "/api/2.0/preview/scim/v2/Me")["userName"]


def upload_tree(base):
    files = {
        "app/app.py": "src/app/app.py",
        "app/app.yaml": "src/app/app.yaml",
        "app/requirements.txt": "src/app/requirements.txt",
        "pipeline/quiz_pipeline.sql": "src/pipeline/quiz_pipeline.sql",
        "jobs/app_power.py": "src/jobs/app_power.py",
    }
    for d in ("app", "pipeline", "jobs"):
        dbx.call("POST", "/api/2.0/workspace/mkdirs", {"path": f"{base}/{d}"})
    for dest, src in files.items():
        content = (ROOT / src).read_bytes()
        is_nb = content.startswith(b"# Databricks notebook source")
        body = {"path": f"{base}/{dest[:-3] if is_nb else dest}",
                "content": base64.b64encode(content).decode(),
                "overwrite": True,
                "format": "SOURCE" if is_nb else "AUTO"}
        if is_nb:
            body["language"] = "PYTHON"
        dbx.call("POST", "/api/2.0/workspace/import", body)
        log("uploaded", dest)


def ensure_pipeline(base):
    spec = {
        "name": PIPELINE, "catalog": "workspace", "schema": "depro_quiz",
        "serverless": True, "continuous": False, "development": False,
        "libraries": [{"file": {"path": f"{base}/pipeline/quiz_pipeline.sql"}}],
    }
    found = [p for p in dbx.call("GET", "/api/2.0/pipelines?max_results=100").get("statuses", [])
             if p["name"] == PIPELINE]
    if found:
        pid = found[0]["pipeline_id"]
        dbx.call("PUT", f"/api/2.0/pipelines/{pid}", {**spec, "id": pid})
        log("pipeline updated", pid)
    else:
        pid = dbx.call("POST", "/api/2.0/pipelines", spec)["pipeline_id"]
        log("pipeline created", pid)
    return pid


def run_pipeline(pid):
    uid = dbx.call("POST", f"/api/2.0/pipelines/{pid}/updates", {})["update_id"]
    while True:
        st = dbx.call("GET", f"/api/2.0/pipelines/{pid}/updates/{uid}")["update"]["state"]
        if st in ("COMPLETED", "FAILED", "CANCELED"):
            log("pipeline update", st)
            if st != "COMPLETED":
                ev = dbx.call("GET", f"/api/2.0/pipelines/{pid}/events?max_results=20")
                for e in ev.get("events", []):
                    if e.get("level") == "ERROR":
                        log("ERROR:", e.get("message"), json.dumps(e.get("error", {}))[:1500])
                raise SystemExit("pipeline failed")
            return
        time.sleep(10)


def ensure_app(base, wid):
    try:
        app = dbx.call("GET", f"/api/2.0/apps/{APP}")
        log("app exists")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        app = dbx.call("POST", "/api/2.0/apps", {
            "name": APP, "description": "DE Pro drill on your phone",
            "resources": [{"name": "sql-warehouse",
                           "sql_warehouse": {"id": wid, "permission": "CAN_USE"}}]})
        log("app created")
    for _ in range(60):
        app = dbx.call("GET", f"/api/2.0/apps/{APP}")
        state = app.get("compute_status", {}).get("state")
        if state == "ACTIVE":
            break
        if state in ("STOPPED", "ERROR"):
            dbx.call("POST", f"/api/2.0/apps/{APP}/start", {})
        time.sleep(10)
    dep = dbx.call("POST", f"/api/2.0/apps/{APP}/deployments",
                   {"source_code_path": f"{base}/app", "mode": "SNAPSHOT"})
    for _ in range(60):
        d = dbx.call("GET", f"/api/2.0/apps/{APP}/deployments/{dep['deployment_id']}")
        s = d.get("status", {}).get("state")
        if s in ("SUCCEEDED", "FAILED", "CANCELLED"):
            log("app deployment", s, d.get("status", {}).get("message", ""))
            break
        time.sleep(10)
    return dbx.call("GET", f"/api/2.0/apps/{APP}")


def grant_app(sp):
    for stmt in (
        f"GRANT USE CATALOG ON CATALOG workspace TO `{sp}`",
        f"GRANT USE SCHEMA, SELECT ON SCHEMA workspace.depro_quiz TO `{sp}`",
        f"GRANT MODIFY ON TABLE workspace.depro_quiz.attempts TO `{sp}`",
        f"GRANT MODIFY ON TABLE workspace.depro_quiz.quiz_sessions TO `{sp}`",
        f"GRANT MODIFY ON TABLE workspace.depro_quiz.user_settings TO `{sp}`",
    ):
        dbx.run_sql(stmt)
        log("ok:", stmt)


def ensure_job(spec):
    jobs = dbx.call("GET", f"/api/2.2/jobs/list?name={spec['name']}").get("jobs", [])
    if jobs:
        jid = jobs[0]["job_id"]
        dbx.call("POST", "/api/2.2/jobs/reset", {"job_id": jid, "new_settings": spec})
        log("job updated", spec["name"])
    else:
        jid = dbx.call("POST", "/api/2.2/jobs/create", spec)["job_id"]
        log("job created", spec["name"])
    return jid


def ensure_jobs(base, pid):
    ensure_job({
        "name": "depro-quiz-ingest",
        "trigger": {"pause_status": "UNPAUSED",
                    "file_arrival": {"url": "/Volumes/workspace/depro_quiz/inbox/"}},
        "tasks": [{"task_key": "refresh_questions", "pipeline_task": {"pipeline_id": pid}}],
    })
    # watchdog: Free Edition sometimes stops the app ("workspace or account status"); restart it
    for action, cron in (("start", "0 30 6 * * ?"), ("ensure", "0 0/15 6-23 * * ?"),
                         ("stop", "0 0 1 * * ?")):
        ensure_job({
            "name": f"depro-quiz-app-{'watchdog' if action == 'ensure' else action}",
            "schedule": {"quartz_cron_expression": cron, "timezone_id": "Asia/Tokyo",
                         "pause_status": "UNPAUSED"},
            "tasks": [{"task_key": action, "notebook_task": {
                "notebook_path": f"{base}/jobs/app_power",
                "base_parameters": {"action": action, "app_name": APP}}}],
        })


def main():
    with_sample = "--sample" in sys.argv[1:]
    base = f"/Workspace/Users/{me()}/depro-quiz"
    log("workspace folder", base)
    dbx.call("GET", "/api/2.0/sql/warehouses")  # auth check
    wid = dbx.warehouse_id()
    sys.argv = ["dbx", "sqlfile", str(ROOT / "sql/00_setup.sql")]
    dbx.main()
    upload_tree(base)
    pid = ensure_pipeline(base)
    if with_sample:
        qs = json.loads((ROOT / "samples/2026-11-07.json").read_text(encoding="utf-8"))
        dbx.call("PUT", f"/api/2.0/fs/files{dbx.INBOX}/2026-11-07.json?overwrite=true",
                 raw=json.dumps(qs, ensure_ascii=False).encode())
        log("sample uploaded")
    run_pipeline(pid)
    sys.argv = ["dbx", "sqlfile", str(ROOT / "sql/10_views.sql")]
    dbx.main()
    app = ensure_app(base, wid)
    grant_app(app["service_principal_client_id"])
    ensure_jobs(base, pid)
    log("app url:", app.get("url"))


if __name__ == "__main__":
    main()
