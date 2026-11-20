"""Privacy and fallback regression tests for the opt-in crash reporter."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from android_src import crash_reporter as report


class ReportTests(unittest.TestCase):
    def test_scrub_strips_urls_tokens_email_paths_and_ip(self):
        raw = "\n".join([
            "error https://r1---sn.googlevideo.com/videoplayback?sig=secret",
            "contact person@example.net for help",
            "file /data/data/org.koteuka404.pymusic/files/keys.json",
            "Authorization: Bearer abcdefghijklmnopqrst",
            "api_key=abcdef-secret",
            "connected to 192.168.1.7",
            "JNI DETECTED ERROR IN APPLICATION",
        ])
        clean = report._scrub(raw)
        self.assertIn("JNI DETECTED ERROR", clean)
        for secret in ("googlevideo.com", "person@example.net", "/data/data/",
                       "abcdefghijklmnopqrst", "abcdef-secret", "192.168.1.7"):
            self.assertNotIn(secret, clean)

    def test_report_selects_valid_crash_file_and_never_uploads_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "session_test.log").write_text("private-key-session", encoding="utf-8")
            Path(tmp, "crash_python_20261120_123456_123.log").write_text(
                "=== PyMusic Kivy exception ===\n"
                "Traceback (most recent call last):\n"
                "ValueError: sample crash at https://private.example/secret\n",
                encoding="utf-8",
            )
            result = report.make_report(tmp)
        self.assertEqual(result["kind"], "crash")
        self.assertIn("ValueError: sample crash", result["details"])
        self.assertNotIn("private-key-session", result["details"])
        self.assertNotIn("private.example", result["details"])
        self.assertEqual(result["schema"], 1)

    def test_native_fault_startup_header_alone_does_not_count_as_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "native_fatal_20261120.log").write_text(
                "PyMusic native fault capture started\n", encoding="utf-8",
            )
            Path(tmp, "pymusic_diag.txt").write_text(
                "[AUDIO] playing\n[CRASH] Error on state\n", encoding="utf-8"
            )
            result = report.make_report(tmp)
        self.assertEqual(result["kind"], "diagnostic")
        self.assertIn("Error on state", result["details"])
        self.assertNotIn("[AUDIO] playing", result["details"])

    def test_endpoint_disallows_http_credentials_and_untrusted_hosts(self):
        previous = report.CRASH_UPLOAD_ENDPOINT
        try:
            for value in (
                "http://x.workers.dev",
                "https://x.workers.dev.attacker.com",
                "https://user:password@x.workers.dev",
                "https://x.workers.dev:8080/report",
            ):
                report.CRASH_UPLOAD_ENDPOINT = value
                self.assertEqual(report._gateway_url(), "", value)
            report.CRASH_UPLOAD_ENDPOINT = "https://pymusic-crash-gateway.example.workers.dev"
            self.assertEqual(
                report._gateway_url(),
                "https://pymusic-crash-gateway.example.workers.dev",
            )
        finally:
            report.CRASH_UPLOAD_ENDPOINT = previous


if __name__ == "__main__":
    unittest.main()
