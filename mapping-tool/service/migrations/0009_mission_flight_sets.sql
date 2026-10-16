-- 0009: missions link the flight sets that were flown for them (ingest flight_set ids, JSON array; NULL = none).
-- Linking a flight set moves a planned/ready mission to flown (app/missions.py).
ALTER TABLE mission ADD COLUMN flight_set_ids JSON NULL AFTER wayline_ids;
