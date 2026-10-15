-- 0006: automatic preparation of every media file uploaded by Pilot 2 (app/ingest.py):
-- category, shot grouping, metadata + thumbnail prepared ahead, and flight sets
-- (shots of one drone with at most FLIGHT_GAP between them) for one-click processing.
CREATE TABLE flight_set (
  id             VARCHAR(36)  NOT NULL,
  workspace_id   VARCHAR(64)  NOT NULL,
  drone          VARCHAR(32)  NOT NULL DEFAULT '',
  kind           VARCHAR(24)  NOT NULL,               -- thermal | multispectral | rgb | video | mixed
  first_capture  DATETIME(3)  NOT NULL,               -- camera time from the file name (local)
  last_capture   DATETIME(3)  NOT NULL,
  shots          INT          NOT NULL DEFAULT 0,
  files          INT          NOT NULL DEFAULT 0,
  created_at     DATETIME(3)  NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  updated_at     DATETIME(3)  NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  PRIMARY KEY (id),
  KEY idx_flight_set_drone (workspace_id, drone, last_capture)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE media_ingest (
  file_id        VARCHAR(64)  NOT NULL,               -- cloud_sample.media_file.file_id
  workspace_id   VARCHAR(64)  NOT NULL,
  category       VARCHAR(16)  NOT NULL,               -- thermal | visible | rgb | multispectral | video | panorama | other
  band           VARCHAR(8)   NULL,                   -- multispectral band: G, R, RE, NIR (F = composite)
  shot_key       VARCHAR(40)  NULL,                   -- DJI_<yyyymmddhhmmss>_<nnnn>: all files of one shot
  captured_at    DATETIME(3)  NULL,
  drone          VARCHAR(32)  NOT NULL DEFAULT '',
  flight_set_id  VARCHAR(36)  NULL,
  meta_ok        TINYINT(1)   NOT NULL DEFAULT 0,
  thumb_ok       TINYINT(1)   NOT NULL DEFAULT 0,
  note           VARCHAR(255) NULL,                   -- why meta/thumbnail is missing
  prepared_at    DATETIME(3)  NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  PRIMARY KEY (file_id),
  KEY idx_ingest_set (flight_set_id, category),
  KEY idx_ingest_shot (shot_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
