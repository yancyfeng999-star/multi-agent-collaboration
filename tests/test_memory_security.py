from __future__ import annotations

import hashlib
import multiprocessing
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from project_memory_lib import (  # noqa: E402
    archive_body_sha256,
    ensure_no_high_confidence_secrets,
    exclusive_lock,
    file_sha256,
    find_high_confidence_secrets,
    normalize_archive_body,
    redact,
)
from protocol_lib import ProtocolError  # noqa: E402


def _hold_lock(path: str, entered: Any, release: Any) -> None:
    with exclusive_lock(Path(path)):
        entered.set()
        release.wait(5)


def _wait_for_lock(path: str, acquired: Any) -> None:
    with exclusive_lock(Path(path)):
        acquired.set()


class SecretSecurityTests(unittest.TestCase):
    def test_redacts_supported_credential_families_without_leaking_values(self) -> None:
        secrets = [
            "bearer-secret-value-123456",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456",
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "db password with spaces",
            "AIzaSyA1234567890_abcdefghijklmnopqrst",
            "ya29.a0AfH6SMB_example_refresh_token_123456",
            "xox" + "b-123456789012-123456789012-abcdefghijklmnop",  # runtime-only fixture
            "session=abc123456789;\n  refresh=def987654321",
        ]
        text = "\n".join(
            [
                f"Authorization: Bearer {secrets[0]}",
                f"jwt={secrets[1]}",
                f"aws_access_key_id={secrets[2]}",
                f"aws_secret_access_key={secrets[3]}",
                "postgresql://app:uri-password-123@db.example.test:5432/app",
                "https://example.test/cb?api_key=query-secret-123456&safe=yes",
                f"password: {secrets[4]}",
                f"google_key={secrets[5]}",
                f"oauth={secrets[6]}",
                f"slack={secrets[7]}",
                f"Cookie: {secrets[8]}",
            ]
        )

        sanitized, count = redact(text)

        self.assertGreaterEqual(count, 11)
        self.assertIn("[REDACTED]", sanitized)
        for secret in secrets:
            self.assertNotIn(secret, sanitized)
        self.assertNotIn("uri-password-123", sanitized)
        self.assertNotIn("query-secret-123456", sanitized)
        self.assertEqual(find_high_confidence_secrets(sanitized), [])

    def test_high_confidence_scan_is_fail_closed_and_ignores_redaction_markers(self) -> None:
        text = "safe prefix\nAuthorization: Bearer bearer-secret-value-123456\nsafe suffix"
        findings = find_high_confidence_secrets(text)
        self.assertTrue(findings)
        with self.assertRaises(ProtocolError):
            ensure_no_high_confidence_secrets(text)
        ensure_no_high_confidence_secrets("Authorization: Bearer [REDACTED]")


class CrossPlatformLockTests(unittest.TestCase):
    def test_exclusive_lock_serializes_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock_path = str(Path(temp) / "memory.lock")
            entered = multiprocessing.Event()
            release = multiprocessing.Event()
            acquired = multiprocessing.Event()
            holder = multiprocessing.Process(target=_hold_lock, args=(lock_path, entered, release))
            waiter = multiprocessing.Process(target=_wait_for_lock, args=(lock_path, acquired))
            holder.start()
            self.assertTrue(entered.wait(3))
            waiter.start()
            time.sleep(0.2)
            self.assertFalse(acquired.is_set())
            release.set()
            self.assertTrue(acquired.wait(3))
            holder.join(3)
            waiter.join(3)
            self.assertEqual(holder.exitcode, 0)
            self.assertEqual(waiter.exitcode, 0)


class ArchiveHashTests(unittest.TestCase):
    def test_archive_body_hash_normalizes_frontmatter_heading_newlines_and_trailing_space(self) -> None:
        archive = "---\r\ndoc_type: conversation_archive\r\n---\r\n\r\n# 完整对话归档\r\n\r\nline one  \r\nline two\t\r\n\r\n"
        expected = "line one\nline two\n"
        self.assertEqual(normalize_archive_body(archive), expected)
        self.assertEqual(archive_body_sha256(archive), hashlib.sha256(expected.encode("utf-8")).hexdigest())

    def test_file_sha256_hashes_bytes_without_text_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "artifact.bin"
            payload = b"\x00archive\r\n\xff"
            path.write_bytes(payload)
            self.assertEqual(file_sha256(path), hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
