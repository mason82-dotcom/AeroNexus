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
    @patch.object(health, "tsdk_check", return_value={"status": "ok"})
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks")
    def test_all_infrastructure_healthy(self, docker, http, tcp, tsdk):
        docker.return_value = {
            service: {"status": "ok"} for service in health.SERVICES
        }
        report = health.build_report()
        self.assertEqual(report["overall_status"], "ok")
        self.assertEqual(report["summary"],
                         {"ok": 20, "warning": 0, "error": 0, "unknown": 1})
        self.assertEqual(report["checks"]["dji.connection"]["status"], "unknown")

    @patch.object(health, "tsdk_check", return_value={"status": "ok"})
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks", return_value={})
    def test_missing_containers_are_errors(self, docker, http, tcp, tsdk):
        report = health.build_report()
        self.assertEqual(report["overall_status"], "error")
        self.assertEqual(report["summary"]["error"], 11)

    @patch.object(health, "tsdk_check", return_value={"status": "ok"})
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks")
    def test_restart_is_warning(self, docker, http, tcp, tsdk):
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


if __name__ == "__main__":
    unittest.main()
