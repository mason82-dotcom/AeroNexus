-- 0010: last contact of each compute agent (claim poll or job heartbeat) for the web status page.
CREATE TABLE agent_seen (
  agent_id      VARCHAR(64)  NOT NULL,
  last_seen     DATETIME(3)  NOT NULL,
  capabilities  JSON         NULL,
  PRIMARY KEY (agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
