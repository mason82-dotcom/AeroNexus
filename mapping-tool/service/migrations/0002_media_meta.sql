-- 0002: cached photo parameters (EXIF + DJI XMP) of media uploaded by Pilot 2
CREATE TABLE media_meta (
  file_id       VARCHAR(64)  NOT NULL,              -- cloud_sample.media_file.file_id
  workspace_id  VARCHAR(64)  NOT NULL,
  meta          JSON         NOT NULL,              -- normalised fields, see app/mediameta.py
  extracted_at  DATETIME(3)  NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
  PRIMARY KEY (file_id),
  KEY idx_meta_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
