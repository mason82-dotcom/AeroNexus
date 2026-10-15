"""Offline tests for the read-only AeroNexus diagnostics."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

MODULE_PATH = pathlib.Path(__file__).with_name("aeronexus_health.py")
SPEC = importlib.util.spec_from_file_location("aeronexus_health", MODULE_PATH)
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)


# Host-dependent checks (backup directory, disks) are replaced in the report
# tests; their logic is tested separately below.
@patch.object(health, "disk_check", new=lambda path: {"status": "ok"})
@patch.object(health, "backup_check", new=lambda *args, **kwargs: {"status": "ok"})
class DiagnosticsTests(unittest.TestCase):
    @patch.object(health, "dji_connection_check", return_value={"status": "unknown"})
    @patch.object(health, "tsdk_check", return_value={"status": "ok"})
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks")
    def test_all_infrastructure_healthy(self, docker, http, tcp, tsdk, dji):
        docker.return_value = {
            service: {"status": "ok"} for service in health.SERVICES
        }
        report = health.build_report()
        self.assertEqual(report["overall_status"], "ok")
        self.assertEqual(report["summary"],
                         {"ok": 27, "warning": 0, "error": 0, "unknown": 2})
        self.assertEqual(report["checks"]["dji.connection"]["status"], "unknown")

    @patch.object(health, "dji_connection_check", return_value={"status": "unknown"})
    @patch.object(health, "tsdk_check", return_value={"status": "ok"})
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks", return_value={})
    def test_missing_containers_are_errors(self, docker, http, tcp, tsdk, dji):
        report = health.build_report()
        self.assertEqual(report["overall_status"], "error")
        self.assertEqual(report["summary"]["error"], 11)

    @patch.object(health, "dji_connection_check", return_value={"status": "unknown"})
    @patch.object(health, "tsdk_check", return_value={"status": "ok"})
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks")
    def test_restart_is_warning(self, docker, http, tcp, tsdk, dji):
        docker.return_value = {
            service: {"status": "ok"} for service in health.SERVICES
        }
        docker.return_value["tsdk"] = {"status": "warning"}
        report = health.build_report()
        self.assertEqual(report["overall_status"], "warning")
        self.assertEqual(report["summary"]["warning"], 1)

    @patch.object(health.subprocess, "run")
    def test_tsdk_missing_sdk_is_warning(self, run):
        run.return_value.stdout = '{"sdk":false,"runner":"box64","version":""}'
        self.assertEqual(health.tsdk_check()["status"], "warning")

    @patch.object(health.subprocess, "run")
    def test_tsdk_invalid_payload_is_error(self, run):
        run.return_value.stdout = '{"sdk":"yes"}'
        self.assertEqual(health.tsdk_check()["status"], "error")


    def test_dji_telemetry_inactive_when_offline(self):
        result = health.dji_telemetry_check({"connection": "offline"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["telemetry"], "inactive")

    def test_dji_telemetry_unknown_with_unverified_client(self):
        result = health.dji_telemetry_check({"connection": "unverified"})
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["telemetry"], "unverified")

    def test_dji_telemetry_unknown_on_broker_failure(self):
        result = health.dji_telemetry_check({"status": "unknown"})
        self.assertEqual(result["status"], "unknown")

    @patch.object(health.subprocess, "run")
    def test_dji_cli_exit_one_with_valid_clients(self, run):
        run.return_value.returncode = 1
        run.return_value.stdout = (
            "Client(a, username=onprem-backend, connected=true)\n"
            "Client(b, username=onprem-backend, connected=true)\n"
        )
        result = health.dji_connection_check()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["connection"], "offline")
        self.assertEqual(result["mqtt_clients"], 2)

    @patch.object(health.subprocess, "run")
    def test_dji_cli_failure_without_clients_stays_unknown(self, run):
        run.return_value.returncode = 1
        run.return_value.stdout = "Error: unavailable\\n"
        self.assertEqual(health.dji_connection_check()["status"], "unknown")

    @patch.object(health.subprocess, "run")
    def test_dji_offline_with_backend_only(self, run):
        run.return_value.stdout = (
            "Client(a, username=onprem-backend, connected=true)\n"
            "Client(b, username=onprem-backend, connected=true)\n"
        )
        result = health.dji_connection_check()
        self.assertEqual(result["connection"], "offline")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mqtt_clients"], 2)

    @patch.object(health.subprocess, "run")
    def test_dji_non_backend_is_unverified(self, run):
        run.return_value.stdout = (
            "Client(a, username=onprem-backend, connected=true)\n"
            "Client(b, username=pilot2, connected=true)\n"
        )
        result = health.dji_connection_check()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["connection"], "unverified")

    @patch.object(health.subprocess, "run")
    def test_dji_no_clients_is_offline(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        self.assertEqual(health.dji_connection_check()["connection"], "offline")

    @patch.object(health.subprocess, "run", side_effect=OSError)
    def test_dji_broker_unavailable_is_unknown(self, run):
        self.assertEqual(health.dji_connection_check()["status"], "unknown")


if __name__ == "__main__":
    unittest.main()


class BackupAndDiskTests(unittest.TestCase):
    NOW = health.datetime(2026, 10, 15, 9, 0, tzinfo=health.timezone.utc)

    def test_backup_age_thresholds(self):
        fresh = health.backup_status("2026-10-15T03:15:03+02:00\nmysql x\n", self.NOW)
        self.assertEqual((fresh["status"], fresh["age_hours"]), ("ok", 7.7))
        one_missed = health.backup_status("2026-10-14T03:15:00+02:00", self.NOW)
        self.assertEqual(one_missed["status"], "warning")
        two_missed = health.backup_status("2026-10-13T03:15:00+02:00", self.NOW)
        self.assertEqual(two_missed["status"], "error")

    def test_backup_missing_or_malformed(self):
        self.assertEqual(health.backup_status("", self.NOW)["reason"], "no_successful_backup")
        self.assertEqual(health.backup_status("yesterday", self.NOW)["status"], "unknown")
        self.assertEqual(health.backup_status("2026-10-15T03:15:03", self.NOW)["reason"],
                         "last_success_without_timezone")

    def test_backup_check_reads_last_success(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(health.backup_check(tmp, self.NOW)["reason"], "no_successful_backup")
            pathlib.Path(tmp, "last-success").write_text("2026-10-15T03:15:03+02:00\n")
            self.assertEqual(health.backup_check(tmp, self.NOW)["status"], "ok")

    def test_disk_thresholds(self):
        self.assertEqual(health.disk_status(100, 40)["status"], "ok")
        self.assertEqual(health.disk_status(100, 14)["status"], "warning")
        self.assertEqual(health.disk_status(100, 4)["status"], "error")
        self.assertEqual(health.disk_status(0, 0)["status"], "unknown")
        self.assertEqual(health.disk_check("/nonexistent/aeronexus")["status"], "error")

    def test_mediamtx_ports_probed(self):
        self.assertEqual(
            {name: port for name, (_, port) in health.TCP.items() if name.startswith("mediamtx")},
            {"mediamtx_rtmp": 1935, "mediamtx_hls": 8888, "mediamtx_webrtc": 8889,
             "mediamtx_rtsp": 8554})
