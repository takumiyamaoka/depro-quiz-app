-- Lakeflow Spark Declarative Pipeline: daily drill files -> question bank
-- Volume layout: /Volumes/workspace/depro_quiz/inbox/YYYY-MM-DD.json (JSON array of questions)

-- Bronze: every file as it arrived (Auto Loader via read_files)
CREATE OR REFRESH STREAMING TABLE questions_bronze (
  CONSTRAINT has_id      EXPECT (question_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT has_answer  EXPECT (answer IN ('A', 'B', 'C', 'D')) ON VIOLATION DROP ROW,
  CONSTRAINT has_choices EXPECT (size(choices) >= 2)
)
COMMENT 'Raw daily drill questions, one row per question per file'
AS SELECT
  *,
  _metadata.file_path         AS source_file,
  _metadata.file_modification_time AS file_time
FROM STREAM read_files(
  '/Volumes/workspace/depro_quiz/inbox/',
  format => 'json',
  multiLine => true,
  schemaHints => 'question_id STRING, drill_date DATE, section STRING, week STRING, topic STRING, stem STRING, choices ARRAY<STRING>, answer STRING, explanation_ja STRING, doc_url STRING, source STRING, version INT'
);

-- Silver: current version of each question (corrections re-sent with the same id and a higher version)
CREATE OR REFRESH STREAMING TABLE questions
COMMENT 'Question bank, latest version per question_id (SCD type 1 via AUTO CDC)';

CREATE FLOW questions_cdc AS AUTO CDC INTO questions
FROM STREAM(questions_bronze)
KEYS (question_id)
SEQUENCE BY struct(version, file_time)
COLUMNS * EXCEPT (_rescued_data)
STORED AS SCD TYPE 1;
