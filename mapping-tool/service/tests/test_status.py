"""Status page warnings (app/status.py build_warnings): disks, backup, host file, containers, jobs, agents."""
import unittest
from datetime import datetime, timedelta, timezone

from app import status

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
GB = 1024 ** 3


def host(**over):
    h = {"at": "2026-10-16T11:58:00Z", "temp_c": 50.0, "mem_total": 8 * GB, "mem_available": 3 * GB,
         "disks": [{"name": "system", "path": "/edge", "exists": True, "total": 250 * GB, "free": 140 * GB,
                    "used": 110 * GB, "device": "/dev/mmcblk0p2"},
                   {"name": "minio", "path": "/mnt/a/minio", "exists": True, "total": 2000 * GB, "free": 400 * GB,
                    "used": 1600 * GB, "device": "/dev/nvme0n1p1"},
                   {"name": "backup", "path": "/mnt/b/backup", "exists": True, "total": 360 * GB, "free": 100 * GB,
                    "used": 260 * GB, "device": "/dev/nvme1n1p2"}],
         "backup": {"last_success": "2026-10-16T03:15:03+02:00", "cron": True},
         "containers": [{"service": "api", "state": "running", "health": ""},
                        {"service": "minio-init", "state": "exited", "health": "", "exit_code": 0}]}
    h.update(over)
    return h


def keys(warnings):
    return [(w["level"], w["key"]) for w in warnings]


class WarningTest(unittest.TestCase):
    def test_all_good(self):
        self.assertEqual(status.build_warnings(host(), [], {}, [], NOW), [])

    def test_missing_and_stale_host_file(self):
        self.assertEqual(keys(status.build_warnings(None, [], {}, [], NOW)), [("warning", "host:missing")])
        old = host(at=(NOW - timedelta(minutes=40)).isoformat())
        w = status.build_warnings(old, [], {}, [], NOW)
        self.assertEqual(keys(w), [("warning", "host:stale")])
        self.assertIn("40 min", w[0]["text"])

    def test_disks(self):
        h = host()
        h["disks"][2]["free"] = 30 * GB                       # 8 % of 360 GB
        h["disks"][1]["free"] = 50 * GB                       # 2.5 % of 2 TB
        self.assertEqual(keys(status.build_warnings(h, [], {}, [], NOW)),
                         [("error", "disk:minio"), ("warning", "disk:backup")])

    def test_unmounted_disk_lands_on_the_sd_card(self):
        h = host()
        h["disks"][1]["device"] = "/dev/mmcblk0p2"
        h["disks"][2] = {"name": "backup", "path": "/mnt/b/backup", "exists": False}
        w = status.build_warnings(h, [], {}, [], NOW)
        self.assertEqual(keys(w), [("error", "disk:minio"), ("error", "disk:backup")])
        self.assertIn("SD-Karte", w[0]["text"])

    def test_backup_age_and_cron(self):
        h = host(backup={"last_success": "2026-10-15T03:15:03+02:00", "cron": False})   # 34.7 h
        self.assertEqual(keys(status.build_warnings(h, [], {}, [], NOW)), [("warning", "backup:cron")])
        h["backup"]["last_success"] = "2026-10-14T20:00:00Z"                             # 40 h
        self.assertEqual(keys(status.build_warnings(h, [], {}, [], NOW)),
                         [("warning", "backup:old"), ("warning", "backup:cron")])
        h["backup"] = {"last_success": "2026-10-01T03:15:00Z", "cron": True}
        self.assertEqual(keys(status.build_warnings(h, [], {}, [], NOW)), [("error", "backup:old")])
        h["backup"] = {"last_success": None, "cron": True}
        self.assertEqual(keys(status.build_warnings(h, [], {}, [], NOW)), [("error", "backup:never")])

    def test_containers_and_services(self):
        h = host(containers=[{"service": "pv", "state": "running", "health": "unhealthy"},
                             {"service": "farming", "state": "exited", "health": "", "exit_code": 137},
                             {"service": "mapping-db-init", "state": "exited", "health": "", "exit_code": 0}])
        services = [{"name": "emqx", "label": "MQTT", "ok": False, "detail": "ConnectionRefusedError"},
                    {"name": "redis", "label": "Redis", "ok": True, "detail": ""}]
        self.assertEqual(keys(status.build_warnings(h, services, {}, [], NOW)),
                         [("error", "service:emqx"), ("error", "container:pv"), ("error", "container:farming")])

    def test_jobs_and_agents(self):
        jobs = {"counts": {"QUEUED": 3}, "failed_recent": [{"id": "j"}]}
        w = status.build_warnings(host(), [], jobs, [], NOW)
        self.assertEqual(keys(w), [("warning", "jobs:failed"), ("info", "agent:offline")])
        self.assertIn("noch nie", w[1]["text"])
        w = status.build_warnings(host(), [], jobs, [{"age_s": 3 * 86400}], NOW)
        self.assertIn("3 Tagen", w[1]["text"])
        self.assertEqual(keys(status.build_warnings(host(), [], {"counts": {"QUEUED": 3}}, [{"age_s": 60}], NOW)), [])

    def test_temperature_and_memory(self):
        h = host(temp_c=80.0, mem_available=GB // 2)
        self.assertEqual(keys(status.build_warnings(h, [], {}, [], NOW)), [("warning", "host:temp"), ("warning", "host:mem")])

    def test_host_file_missing_is_none(self):
        self.assertIsNone(status.read_host("/nonexistent/host.json"))


if __name__ == "__main__":
    unittest.main()
