"""Offline tests for the read-only AeroNexus diagnostics."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

MODULE_PATH = pathlib.Path(__file__).with_name("aeronexus_health.py")
SPEC = importlib.util.spec_from_file_location("aeronexus_health", MODULE_PATH)
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)


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
                         {"ok": 20, "warning": 0, "error": 0, "unknown": 2})
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
