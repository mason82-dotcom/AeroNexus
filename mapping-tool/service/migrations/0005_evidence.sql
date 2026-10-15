-- 0005: evidence register (Beweissicherung) for media uploaded by Pilot 2.
-- One row per media file: SHA-256 of the object as stored in MinIO, sealed once.
-- Rows form a hash chain (prev_hash -> record_hash, see app/evidence.py), so a
-- later change of any row, or a deleted row, breaks verification of the chain.
-- Rows are never updated or deleted by the service.
CREATE TABLE evidence_record (
  seq           BIGINT        NOT NULL,              -- chain position, 1..n without gaps
  file_id       VARCHAR(64)   NOT NULL,              -- cloud_sample.media_file.file_id
  workspace_id  VARCHAR(64)   NOT NULL,
  file_name     VARCHAR(255)  NOT NULL,
  object_key    VARCHAR(1000) NOT NULL,
  size_bytes    BIGINT        NOT NULL,
  sha256        CHAR(64)      NOT NULL,
  drone         VARCHAR(32)   NOT NULL DEFAULT '',
  payload       VARCHAR(32)   NOT NULL DEFAULT '',
  uploaded_at   DATETIME(3)   NULL,                  -- media_file.create_time (UTC)
  sealed_at     DATETIME(3)   NOT NULL,              -- UTC, when the hash was taken
  prev_hash     CHAR(64)      NOT NULL,
  record_hash   CHAR(64)      NOT NULL,
  PRIMARY KEY (seq),
  UNIQUE KEY uq_evidence_file (file_id),
  KEY idx_evidence_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
