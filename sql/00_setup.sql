-- DE Pro quiz app: one-time setup (run in SQL editor or via Statement Execution API)
CREATE SCHEMA IF NOT EXISTS workspace.depro_quiz
  COMMENT 'DE Pro study app: question bank (Lakeflow) + answer log (app)';

-- Landing zone for daily drill files written by Claude (one JSON file per day)
CREATE VOLUME IF NOT EXISTS workspace.depro_quiz.inbox
  COMMENT 'Daily drill question files: YYYY-MM-DD.json';

-- Answer log written by the Databricks App (service principal needs MODIFY)
CREATE TABLE IF NOT EXISTS workspace.depro_quiz.attempts (
  attempt_id   STRING    NOT NULL,
  question_id  STRING    NOT NULL,
  chosen       STRING    NOT NULL COMMENT 'A/B/C/D',
  is_correct   BOOLEAN   NOT NULL,
  confidence   STRING    COMMENT 'sure / unsure / guess',
  mode         STRING    COMMENT 'today / review / section / random',
  answered_at  TIMESTAMP NOT NULL
)
CLUSTER BY AUTO
COMMENT 'One row per answer submitted from the quiz app';

-- In-progress quiz sets so the app can resume after "back" or a closed browser
CREATE TABLE IF NOT EXISTS workspace.depro_quiz.quiz_sessions (
  session_id   STRING    NOT NULL,
  user_email   STRING,
  mode         STRING,
  question_ids STRING    COMMENT 'comma-separated, in display order',
  total        INT,
  idx          INT       COMMENT 'number of questions answered',
  score        INT,
  status       STRING    COMMENT 'active / done / abandoned',
  started_at   TIMESTAMP,
  updated_at   TIMESTAMP
)
COMMENT 'Quiz sets started in the app, for resume';

-- Per-user app settings (Setting page)
CREATE TABLE IF NOT EXISTS workspace.depro_quiz.user_settings (
  user_email         STRING NOT NULL,
  review_n           INT,
  random_n           INT,
  require_confidence BOOLEAN,
  updated_at         TIMESTAMP
)
COMMENT 'Quiz app settings per user';
