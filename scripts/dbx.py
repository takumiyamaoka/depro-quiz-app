#!/usr/bin/env python3
"""Small Databricks REST helper for Claude sessions (no SDK needed).

Env: DATABRICKS_HOST, DATABRICKS_TOKEN, optional DATABRICKS_WAREHOUSE_ID.

  dbx.py upload <local.json> [YYYY-MM-DD]   put a daily drill file into the inbox Volume
  dbx.py sql "<statement>"                    run SQL on the warehouse, print rows as JSON
  dbx.py sqlfile <file.sql>                   run each ;-separated statement of a file
  dbx.py results [YYYY-MM-DD]                 answers for a drill date (default: today JST)
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HOST = os.environ["DATABRICKS_HOST"].rstrip("/")
TOKEN = os.environ["DATABRICKS_TOKEN"]
INBOX = "/Volumes/workspace/depro_quiz/inbox"
JST = timezone(timedelta(hours=9))


def call(method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(HOST + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if raw is not None:
        req.add_header("Content-Type", "application/octet-stream")
    elif body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else {}


def warehouse_id():
    wid = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if wid:
        return wid
    return call("GET", "/api/2.0/sql/warehouses")["warehouses"][0]["id"]


def run_sql(stmt):
    r = call("POST", "/api/2.0/sql/statements", {
        "statement": stmt, "warehouse_id": warehouse_id(), "wait_timeout": "50s",
        "format": "JSON_ARRAY", "disposition": "INLINE"})
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(3)
        r = call("GET", f"/api/2.0/sql/statements/{r['statement_id']}")
    if r["status"]["state"] != "SUCCEEDED":
        raise SystemExit(f"SQL failed: {r['status']}\n{stmt[:300]}")
    cols = [c["name"] for c in r.get("manifest", {}).get("schema", {}).get("columns", [])]
    rows = r.get("result", {}).get("data_array", []) or []
    return [dict(zip(cols, row)) for row in rows]


def validate(qs):
    assert isinstance(qs, list) and qs, "file must be a non-empty JSON array"
    need = {"question_id", "drill_date", "section", "topic", "stem", "choices",
            "answer", "explanation_ja", "doc_url", "source", "version"}
    for q in qs:
        missing = need - q.keys()
        assert not missing, f"{q.get('question_id')}: missing {missing}"
        assert q["answer"] in "ABCD"[: len(q["choices"])], f"{q['question_id']}: bad answer"
        assert re.fullmatch(r"S[1-9]", q["section"]), f"{q['question_id']}: bad section"


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "upload":
        qs = json.load(open(args[0], encoding="utf-8"))
        validate(qs)
        day = args[1] if len(args) > 1 else qs[0]["drill_date"]
        dest = f"{INBOX}/{day}.json"
        call("PUT", f"/api/2.0/fs/files{dest}?overwrite=true",
             raw=json.dumps(qs, ensure_ascii=False).encode())
        print(f"uploaded {len(qs)} questions -> {dest}")
    elif cmd == "sql":
        print(json.dumps(run_sql(args[0]), ensure_ascii=False, indent=1))
    elif cmd == "sqlfile":
        text = open(args[0], encoding="utf-8").read()
        text = re.sub(r"--[^\n]*", "", text)
        # split on ; outside $$ blocks
        parts, buf, in_dollar = [], "", False
        for tok in re.split(r"(\$\$|;)", text):
            if tok == "$$":
                in_dollar = not in_dollar
            if tok == ";" and not in_dollar:
                parts.append(buf); buf = ""
            else:
                buf += tok
        parts.append(buf)
        for p in (p.strip() for p in parts):
            if p:
                run_sql(p)
                print("ok:", p.splitlines()[0][:80])
    elif cmd == "results":
        day = args[0] if args else datetime.now(JST).date().isoformat()
        print(json.dumps(run_sql(
            "SELECT question_id, section, topic, chosen, is_correct, confidence, answered_at "
            "FROM workspace.depro_quiz.attempts_enriched "
            f"WHERE drill_date = DATE'{day}' ORDER BY question_id, answered_at"),
            ensure_ascii=False, indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
