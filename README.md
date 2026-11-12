# depro-quiz-app

DE Pro (Databricks Certified Data Engineer Professional) drill app on Databricks Free Edition.

Phone-first drill app. Claude drops 5 questions per weekday into a UC Volume, Lakeflow ingests them,
The learner answers in the app, answers land in Delta and feed a Metric View.

```
Claude (8:50 JST) --Files API--> /Volumes/workspace/depro_quiz/inbox/YYYY-MM-DD.json
   -> job depro-quiz-ingest (file arrival trigger)
   -> pipeline depro-quiz-pipeline: questions_bronze (read_files) -> questions (AUTO CDC, SCD1)
App depro-quiz (Streamlit, app service principal) -> reads questions/review_queue/quiz_metrics,
   writes attempts / quiz_sessions (resume) / user_settings
Jobs depro-quiz-app-start 06:30, depro-quiz-app-watchdog every 15 min 06:00-23:59 (restart if stopped),
   depro-quiz-app-stop 01:00 JST
```

## App screens (phone-first, bottom dock)
- **Quiz**: today's drill / review queue / by section / random; "‹ 戻る" and resume of an unfinished set.
- **Dashboard**: total answers, accuracy, latest drill progress, study streak, per-day drill status,
  accuracy by section, weakest topics, daily accuracy (all from the `quiz_metrics` Metric View).
- **Setting**: question counts, require a confidence choice before answering (per user).

## Exam topics this project exercises
Auto Loader (`read_files`), Lakeflow Declarative Pipelines + expectations, AUTO CDC (SCD type 1),
Metric Views, Unity Catalog grants to an app service principal, Lakeflow Jobs (file-arrival and cron
triggers), Databricks Apps, Declarative Automation Bundles (`databricks.yml`).

## Daily drill file format (contract for the daily-question task)
One JSON array per day, UTF-8, 5 objects:

| field | type | notes |
|---|---|---|
| question_id | string | `YYYY-MM-DD-n` (n = 1..5). Re-send same id with higher `version` to correct a question |
| drill_date | string | `YYYY-MM-DD` (JST) |
| section | string | `S1`..`S9` (exam guide sections) |
| week | string | study week, `W1`..`W16` |
| topic | string | short English topic, reused across days for weak-topic stats |
| stem | string | English question text |
| choices | string[] | 4 options, no "A." prefix |
| answer | string | `A`..`D` |
| explanation_ja | string | Japanese explanation (why right, why others wrong) |
| doc_url | string | docs.databricks.com URL |
| source | string | `daily` / `mock` / `diagnostic` |
| version | int | 1 for new |

Upload: `python3 scripts/dbx.py upload <file.json>` (needs DATABRICKS_HOST / DATABRICKS_TOKEN).
Read results: `python3 scripts/dbx.py results 2026-11-09`.

## Deploy
- With Databricks CLI: `databricks bundle deploy` (databricks.yml), then run `sql/00_setup.sql`, `sql/10_views.sql` and the grants in `scripts/deploy.py:grant_app`.
- Without CLI: `python3 scripts/deploy.py --sample` (REST only, idempotent).

## License
MIT (see LICENSE).
