-- 0001: mapping jobs, results and map layers (see docs/adr-001-architektur.md)
-- All times are UTC.

CREATE TABLE mapping_job (
  id            CHAR(36)      NOT NULL,
  workspace_id  VARCHAR(64)   NOT NULL,
  name          VARCHAR(200)  NOT NULL,
  status        VARCHAR(16)   NOT NULL,              -- QUEUED CLAIMED RUNNING DONE FAILED
  image_keys    JSON          NOT NULL,              -- MinIO object keys in the media bucket
  options       JSON          NOT NULL,              -- {"profile": "...", "odm": {...}}
  created_by    VARCHAR(64)   NOT NULL,
  agent_id      VARCHAR(64)   NULL,
  lease_until   DATETIME(3)   NULL,
  attempts      INT           NOT NULL DEFAULT 0,    -- number of expired leases
  progress      DECIMAL(5,2)  NOT NULL DEFAULT 0,
  message       VARCHAR(500)  NULL,
  error         VARCHAR(2000) NULL,
  created_at    DATETIME(3)   NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  updated_at    DATETIME(3)   NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  claimed_at    DATETIME(3)   NULL,
  finished_at   DATETIME(3)   NULL,
  PRIMARY KEY (id),
  KEY idx_status_created (status, created_at),
  KEY idx_workspace_created (workspace_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE mapping_result (
  id          BIGINT        NOT NULL AUTO_INCREMENT,
  job_id      CHAR(36)      NOT NULL,
  kind        VARCHAR(32)   NOT NULL,                -- orthophoto_cog dsm_cog dtm_cog report log other
  object_key  VARCHAR(512)  NOT NULL,                -- key in the mapping-results bucket
  sha256      CHAR(64)      NULL,
  size_bytes  BIGINT        NULL,
  created_at  DATETIME(3)   NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  PRIMARY KEY (id),
  UNIQUE KEY uk_job_key (job_id, object_key),
  CONSTRAINT fk_result_job FOREIGN KEY (job_id) REFERENCES mapping_job (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE map_layer (
  id            CHAR(36)      NOT NULL,
  workspace_id  VARCHAR(64)   NOT NULL,
  job_id        CHAR(36)      NULL,
  name          VARCHAR(200)  NOT NULL,
  layer_type    VARCHAR(16)   NOT NULL,              -- xyz (later: pmtiles, wmts)
  bucket        VARCHAR(64)   NOT NULL,
  tile_prefix   VARCHAR(512)  NOT NULL,              -- e.g. <jobId>/tiles -> <prefix>/{z}/{x}/{y}.<format>
  tile_format   VARCHAR(8)    NOT NULL DEFAULT 'png',
  min_zoom      INT           NOT NULL,
  max_zoom      INT           NOT NULL,
  bounds_wgs84  JSON          NULL,                  -- [west, south, east, north]
  crs           VARCHAR(32)   NULL,                  -- CRS of the source raster
  attribution   VARCHAR(200)  NULL,
  opacity       DECIMAL(3,2)  NOT NULL DEFAULT 0.80,
  created_at    DATETIME(3)   NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  PRIMARY KEY (id),
  KEY idx_layer_workspace (workspace_id, created_at),
  CONSTRAINT fk_layer_job FOREIGN KEY (job_id) REFERENCES mapping_job (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
