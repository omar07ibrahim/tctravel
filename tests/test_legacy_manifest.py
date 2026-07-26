from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.verify_legacy import (
    MAX_DECODED_MEDIA_BYTES,
    MAX_ENCODED_MEDIA_BYTES,
    MAX_LEGACY_FILE_BYTES,
    MAX_LEGACY_TOTAL_BYTES,
    MAX_MANIFEST_BYTES,
    VerificationFailure,
    verify,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "legacy.manifest.json"


class LegacyManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        manifest = self._read_json(REPOSITORY_ROOT / MANIFEST_NAME)
        shutil.copy2(
            REPOSITORY_ROOT / MANIFEST_NAME,
            self.root / MANIFEST_NAME,
        )
        for entry in manifest["snapshot"]["inventory"]:
            shutil.copy2(
                REPOSITORY_ROOT / entry["path"],
                self.root / entry["path"],
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_manifest(self, manifest: dict[str, object]) -> None:
        (self.root / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    def _refresh_inventory_entry(self, path_name: str) -> None:
        manifest = self._read_json(self.root / MANIFEST_NAME)
        payload = (self.root / path_name).read_bytes()
        for entry in manifest["snapshot"]["inventory"]:
            if entry["path"] == path_name:
                entry["bytes"] = len(payload)
                entry["sha256"] = hashlib.sha256(payload).hexdigest()
                break
        else:
            self.fail("test fixture path is not in the manifest")
        manifest["snapshot"]["legacy_total_bytes"] = sum(
            entry["bytes"] for entry in manifest["snapshot"]["inventory"]
        )
        self._write_manifest(manifest)

    def assert_verification_code(self, expected_code: str) -> None:
        with self.assertRaises(VerificationFailure) as context:
            verify(self.root)
        self.assertEqual(context.exception.code, expected_code)

    def test_committed_snapshot_verifies(self) -> None:
        report = verify(REPOSITORY_ROOT)
        self.assertEqual(report.legacy_files, 9)
        self.assertEqual(report.legacy_bytes, 740710)
        self.assertEqual(report.embedded_jpeg_occurrences, 6)
        self.assertEqual(report.embedded_jpeg_unique_payloads, 3)
        self.assertEqual(report.third_party_mark_occurrences, 6)
        self.assertEqual(report.third_party_mark_unique_payloads, 1)

    def test_documented_check_command_succeeds(self) -> None:
        completed = subprocess.run(
            [sys.executable, "tools/verify_legacy.py", "--check"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertTrue(
            completed.stdout.startswith("TCTravel legacy attestation: PASS\n")
        )
        self.assertNotIn("@", completed.stdout)
        self.assertNotIn("+994", completed.stdout)

    def test_byte_tamper_fails_closed(self) -> None:
        target = self.root / "4-days-tour_67186623.html"
        target.write_bytes(target.read_bytes() + b"\n")
        self.assert_verification_code("size_mismatch")

    def test_same_size_tamper_fails_closed(self) -> None:
        target = self.root / "Baku_83519103.html"
        payload = target.read_bytes()
        target.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
        self.assert_verification_code("checksum_mismatch")

    def test_missing_legacy_file_fails_closed(self) -> None:
        (self.root / "Azerbaijan_31890772.html").unlink()
        self.assert_verification_code("inventory_mismatch")

    def test_additional_legacy_html_fails_closed(self) -> None:
        (self.root / "unexpected_legacy.html").write_text(
            "<!doctype html><title>fixture</title>\n",
            encoding="utf-8",
        )
        self.assert_verification_code("inventory_mismatch")

    def test_case_variant_legacy_html_fails_closed(self) -> None:
        (self.root / "unexpected.HTML").write_text(
            "<!doctype html><title>fixture</title>\n",
            encoding="utf-8",
        )
        self.assert_verification_code("inventory_mismatch")

    def test_oversized_manifest_fails_before_json_parsing(self) -> None:
        (self.root / MANIFEST_NAME).write_bytes(b"{" + b" " * MAX_MANIFEST_BYTES)
        self.assert_verification_code("resource_limit_exceeded")

    def test_oversized_declared_total_fails_closed(self) -> None:
        manifest = self._read_json(self.root / MANIFEST_NAME)
        manifest["snapshot"]["legacy_total_bytes"] = (
            MAX_LEGACY_TOTAL_BYTES + 1
        )
        self._write_manifest(manifest)
        self.assert_verification_code("resource_limit_exceeded")

    def test_oversized_legacy_file_fails_before_reading(self) -> None:
        target = self.root / "Baku_83519103.html"
        target.write_bytes(b"x" * (MAX_LEGACY_FILE_BYTES + 1))
        self.assert_verification_code("resource_limit_exceeded")

    def test_oversized_encoded_media_fails_closed(self) -> None:
        metadata_path = self.root / "site.json"
        metadata = self._read_json(metadata_path)
        metadata["settings"] += (
            "data:image/jpeg;base64,"
            + "A" * (MAX_ENCODED_MEDIA_BYTES + 4)
        )
        metadata_path.write_text(
            json.dumps(metadata, separators=(",", ":")),
            encoding="utf-8",
        )
        self._refresh_inventory_entry("site.json")
        self.assert_verification_code("resource_limit_exceeded")

    def test_oversized_decoded_media_fails_closed(self) -> None:
        metadata_path = self.root / "site.json"
        metadata = self._read_json(metadata_path)
        encoded_bytes = ((MAX_DECODED_MEDIA_BYTES + 3) // 3) * 4
        self.assertLess(encoded_bytes, MAX_ENCODED_MEDIA_BYTES)
        metadata["settings"] += (
            "data:image/jpeg;base64," + "A" * encoded_bytes
        )
        metadata_path.write_text(
            json.dumps(metadata, separators=(",", ":")),
            encoding="utf-8",
        )
        self._refresh_inventory_entry("site.json")
        self.assert_verification_code("resource_limit_exceeded")

    def test_manifest_schema_drift_fails_closed(self) -> None:
        manifest = self._read_json(self.root / MANIFEST_NAME)
        manifest["unreviewed_extension"] = {}
        self._write_manifest(manifest)
        self.assert_verification_code("manifest_schema_invalid")

    def test_generator_schema_drift_fails_after_checksum_rebase(self) -> None:
        metadata_path = self.root / "site.json"
        metadata = self._read_json(metadata_path)
        del metadata["datePublished"]
        metadata_path.write_text(
            json.dumps(metadata, separators=(",", ":")),
            encoding="utf-8",
        )
        self._refresh_inventory_entry("site.json")
        self.assert_verification_code("generator_metadata_mismatch")

    def test_jpeg_duplicate_map_drift_fails_after_checksum_rebase(self) -> None:
        metadata_path = self.root / "site.json"
        metadata = self._read_json(metadata_path)
        pattern = re.compile(
            r"data:image/jpeg;base64,([A-Za-z0-9+/]+={0,2})"
        )
        payloads = pattern.findall(metadata["settings"])
        self.assertGreaterEqual(len(payloads), 2)
        replacement = next(
            payload for payload in payloads[1:] if payload != payloads[0]
        )
        metadata["settings"] = metadata["settings"].replace(
            payloads[0],
            replacement,
            1,
        )
        metadata_path.write_text(
            json.dumps(metadata, separators=(",", ":")),
            encoding="utf-8",
        )
        self._refresh_inventory_entry("site.json")
        self.assert_verification_code("embedded_media_mismatch")

    def test_svg_count_drift_fails_after_checksum_rebase(self) -> None:
        path_name = "TCtravel_30109399.html"
        target = self.root / path_name
        payload = target.read_bytes()
        marker = b"data:image/svg+xml;base64,"
        self.assertIn(marker, payload)
        target.write_bytes(
            payload.replace(
                marker,
                b"data:image/svg-disabled;base64,",
                1,
            )
        )
        self._refresh_inventory_entry(path_name)
        self.assert_verification_code("third_party_mark_mismatch")


if __name__ == "__main__":
    unittest.main()
