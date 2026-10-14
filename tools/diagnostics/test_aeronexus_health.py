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
    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks")
    def test_all_infrastructure_healthy(self, docker, http, tcp):
        docker.return_value = {
            service: {"status": "ok"} for service in health.SERVICES
        }
        report = health.build_report()
        self.assertEqual(report["overall_status"], "ok")
        self.assertEqual(report["summary"],
                         {"ok": 19, "warning": 0, "error": 0, "unknown": 1})
        self.assertEqual(report["checks"]["dji.connection"]["status"], "unknown")

    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks", return_value={})
    def test_missing_containers_are_errors(self, docker, http, tcp):
        report = health.build_report()
        self.assertEqual(report["overall_status"], "error")
        self.assertEqual(report["summary"]["error"], 11)

    @patch.object(health, "tcp_check", return_value={"status": "ok"})
    @patch.object(health, "http_check", return_value={"status": "ok"})
    @patch.object(health, "docker_checks")
    def test_restart_is_warning(self, docker, http, tcp):
        docker.return_value = {
            service: {"status": "ok"} for service in health.SERVICES
        }
        docker.return_value["tsdk"] = {"status": "warning"}
        report = health.build_report()
        self.assertEqual(report["overall_status"], "warning")
        self.assertEqual(report["summary"]["warning"], 1)


if __name__ == "__main__":
    unittest.main()
