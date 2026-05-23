-- content_pipeline state — runtime source of truth (Notion is only a mirror).
-- All timestamps are ISO-8601 UTC strings.

CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id              TEXT PRIMARY KEY,
  notion_page_id      TEXT,                       -- parent card; one card → many runs
  tenant              TEXT NOT NULL,
  owner_user_id       TEXT NOT NULL,              -- who owns the card
  actor_user_id       TEXT,                       -- who acted last (SMM vs owner)
  chat_id             TEXT,                       -- where to deliver async results
  plan                TEXT NOT NULL,              -- avatar | selfie | broll
  stage               TEXT NOT NULL,              -- script | cover | voice | avatar | done
  status              TEXT NOT NULL,              -- running_job | waiting_user | waiting_input | waiting_confirm | completed | cancelled | failed
  stage_version       INTEGER NOT NULL DEFAULT 1, -- optimistic concurrency / stale-button guard
  active              INTEGER NOT NULL DEFAULT 1,

  paid_gate           TEXT NOT NULL DEFAULT 'none', -- none | pending | confirmed | spent
  current_job_id      TEXT,                       -- external provider job id (HeyGen etc.)

  notion_status       TEXT,
  notion_synced_at    TEXT,
  notion_sync_pending INTEGER NOT NULL DEFAULT 0,

  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_notion       ON pipeline_runs(notion_page_id);
CREATE INDEX IF NOT EXISTS idx_runs_owner_active ON pipeline_runs(owner_user_id, active);

CREATE TABLE IF NOT EXISTS pipeline_artifacts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL,
  kind         TEXT NOT NULL,   -- script | cover | voice | avatar_video | broll_zip | final_video
  path         TEXT,
  url          TEXT,
  text_content TEXT,
  meta_json    TEXT,
  created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run ON pipeline_artifacts(run_id);

-- Audit log of facts (NOT event-sourcing replay). Cheap, but priceless when a
-- paid gate or stale button is disputed.
CREATE TABLE IF NOT EXISTS pipeline_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  event_type    TEXT NOT NULL,  -- run_created | stage_advanced | user_approved | stage_skipped | paid_gate_shown | paid_confirmed | job_started | stale_action_rejected
  from_stage    TEXT,
  to_stage      TEXT,
  actor_user_id TEXT,
  payload_json  TEXT,
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON pipeline_events(run_id);
