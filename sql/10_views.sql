-- Views on top of the pipeline output (questions) and the app log (attempts)

-- Every answer with its question attributes
CREATE OR REPLACE VIEW workspace.depro_quiz.attempts_enriched AS
SELECT
  a.*,
  q.section, q.week, q.topic, q.drill_date, q.source,
  to_date(from_utc_timestamp(a.answered_at, 'Asia/Tokyo')) AS answered_date_jst
FROM workspace.depro_quiz.attempts a
JOIN workspace.depro_quiz.questions q USING (question_id);

-- Review queue: never answered first, then last answer wrong, then lucky guesses, then stale (7+ days)
CREATE OR REPLACE VIEW workspace.depro_quiz.review_queue AS
WITH last_attempt AS (
  SELECT * FROM workspace.depro_quiz.attempts
  QUALIFY row_number() OVER (PARTITION BY question_id ORDER BY answered_at DESC) = 1
)
SELECT
  q.question_id, q.section, q.topic, q.drill_date,
  l.answered_at AS last_answered_at, l.is_correct AS last_correct, l.confidence AS last_confidence,
  CASE
    WHEN l.question_id IS NULL                    THEN 1
    WHEN NOT l.is_correct                         THEN 2
    WHEN l.confidence IN ('guess', 'unsure')      THEN 3
    WHEN l.answered_at < current_timestamp() - INTERVAL 7 DAYS THEN 4
  END AS priority
FROM workspace.depro_quiz.questions q
LEFT JOIN last_attempt l USING (question_id)
WHERE l.question_id IS NULL
   OR NOT l.is_correct
   OR l.confidence IN ('guess', 'unsure')
   OR l.answered_at < current_timestamp() - INTERVAL 7 DAYS;

-- Metric View: one definition of "accuracy" reused by the app and dashboards
CREATE OR REPLACE VIEW workspace.depro_quiz.quiz_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 0.1
source: workspace.depro_quiz.attempts_enriched
dimensions:
  - name: section
    expr: section
  - name: week
    expr: week
  - name: topic
    expr: topic
  - name: answered_date
    expr: answered_date_jst
  - name: confidence
    expr: confidence
measures:
  - name: answers
    expr: COUNT(1)
  - name: correct
    expr: COUNT_IF(is_correct)
  - name: accuracy
    expr: AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END)
  - name: questions_seen
    expr: COUNT(DISTINCT question_id)
$$;
