-- Mission planning for remote controller operations (app/missions.py): what is flown when, where, by whom,
-- with which routes, and the pre-flight checklist. Times are UTC.
CREATE TABLE mission (
  id            CHAR(36)      NOT NULL PRIMARY KEY,
  workspace_id  VARCHAR(64)   NOT NULL,
  title         VARCHAR(120)  NOT NULL,
  purpose       VARCHAR(16)   NOT NULL,
  planned_start DATETIME(3)   NOT NULL,
  duration_min  INT           NULL,
  site          VARCHAR(120)  NOT NULL DEFAULT '',
  notes         TEXT          NULL,
  drone_sn      VARCHAR(32)   NULL,
  pilot         VARCHAR(64)   NULL,
  wayline_ids   JSON          NOT NULL,
  checklist     JSON          NOT NULL,
  status        VARCHAR(16)   NOT NULL,
  created_by    VARCHAR(64)   NOT NULL DEFAULT '',
  created_at    DATETIME(3)   NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  updated_at    DATETIME(3)   NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  status_at     DATETIME(3)   NULL,
  KEY idx_mission_ws_start (workspace_id, planned_start)
);
