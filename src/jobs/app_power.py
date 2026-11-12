# Databricks notebook source
# Start or stop the quiz app on a schedule (Free Edition stops apps after 24h; we also stop it at night to save quota).
# Job parameter: action = start | stop | ensure (start only if stopped; used by the 15-min watchdog)

# COMMAND ----------
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("action", "start")
dbutils.widgets.text("app_name", "depro-quiz")
action = dbutils.widgets.get("action")
app_name = dbutils.widgets.get("app_name")

w = WorkspaceClient()
state = w.apps.get(app_name).compute_status.state.value
print(f"{app_name}: compute state before = {state}")

if action == "ensure":
    if state in ("STOPPED", "ERROR"):
        w.apps.start(app_name).result()
elif action == "start" and state != "ACTIVE":
    w.apps.start(app_name).result()
elif action == "start":
    # already running: stop + start resets the 24h timer before the day begins
    w.apps.stop(app_name).result()
    w.apps.start(app_name).result()
elif action == "stop" and state not in ("STOPPED", "STOPPING"):
    w.apps.stop(app_name).result()

print(f"{app_name}: compute state after = {w.apps.get(app_name).compute_status.state.value}")
