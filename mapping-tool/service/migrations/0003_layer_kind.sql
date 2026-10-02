-- 0003: several layers per job (orthophoto + vegetation indices of the Farming Guide)
ALTER TABLE map_layer ADD COLUMN kind VARCHAR(32) NOT NULL DEFAULT 'orthophoto' AFTER layer_type;
ALTER TABLE map_layer ADD COLUMN legend JSON NULL AFTER opacity;
ALTER TABLE map_layer ADD COLUMN stats JSON NULL AFTER legend;
ALTER TABLE map_layer ADD COLUMN is_relative TINYINT(1) NOT NULL DEFAULT 0 AFTER stats;
