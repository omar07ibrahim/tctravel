#!/usr/bin/env python3
"""Verify the quarantined TCTravel legacy snapshot without exposing its content."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


MANIFEST_NAME = "legacy.manifest.json"
SITE_METADATA_NAME = "site.json"
EXPECTED_LEGACY_FILE_COUNT = 9
EXPECTED_HTML_FILE_COUNT = 8
EXPECTED_JPEG_OCCURRENCES = 6
EXPECTED_JPEG_UNIQUE_PAYLOADS = 3
EXPECTED_THIRD_PARTY_MARK_OCCURRENCES = 6
EXPECTED_THIRD_PARTY_MARK_UNIQUE_PAYLOADS = 1

MAX_MANIFEST_BYTES = 64 * 1024
MAX_LEGACY_FILE_BYTES = 1024 * 1024
MAX_LEGACY_TOTAL_BYTES = 2 * 1024 * 1024
MAX_ENCODED_MEDIA_BYTES = 512 * 1024
MAX_DECODED_MEDIA_BYTES = 256 * 1024

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
PHONE_LIKE_PATTERN = re.compile(
    r"(?<!\w)(?:\+?\d(?:[\s().-]*\d){6,})(?!\w)"
)

IGNORED_TEXT_ELEMENTS = frozenset({"script", "style", "template"})

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "snapshot",
        "generator_contract",
        "embedded_jpeg_contract",
        "redacted_risk_contract",
    }
)
SNAPSHOT_KEYS = frozenset(
    {"legacy_file_count", "legacy_total_bytes", "inventory"}
)
INVENTORY_ENTRY_KEYS = frozenset({"path", "bytes", "sha256"})
GENERATOR_KEYS = frozenset(
    {"metadata_path", "app_version", "is_trial", "unpublished"}
)
UNPUBLISHED_KEYS = frozenset(
    {"date_published", "public_url", "custom_domain_url", "server_site_id"}
)
JPEG_CONTRACT_KEYS = frozenset(
    {
        "source_path",
        "json_field",
        "mime_type",
        "total_occurrences",
        "unique_payloads",
    }
)
JPEG_PAYLOAD_KEYS = frozenset(
    {"sha256", "decoded_bytes", "locations"}
)
JPEG_LOCATION_KEYS = frozenset({"path", "occurrences"})
RISK_KEYS = frozenset(
    {
        "legacy_prose_documents",
        "visible_contact_surface_documents",
        "visible_email_like_occurrences",
        "visible_phone_like_occurrences",
        "embedded_jpeg_occurrences",
        "embedded_jpeg_unique_payloads",
        "third_party_mark_occurrences",
        "third_party_mark_unique_payloads",
    }
)


class VerificationFailure(Exception):
    """A deliberately redacted verification failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerificationReport:
    legacy_files: int
    legacy_bytes: int
    legacy_prose_documents: int
    visible_contact_surface_documents: int
    visible_email_like_occurrences: int
    visible_phone_like_occurrences: int
    embedded_jpeg_occurrences: int
    embedded_jpeg_unique_payloads: int
    third_party_mark_occurrences: int
    third_party_mark_unique_payloads: int

    def output_lines(self) -> tuple[str, ...]:
        """Return redacted, stable output with no source values or paths."""

        return (
            "TCTravel legacy attestation: PASS",
            (
                "snapshot: "
                f"legacy_files={self.legacy_files} "
                f"legacy_bytes={self.legacy_bytes}"
            ),
            f"risk: legacy_prose_documents={self.legacy_prose_documents}",
            (
                "risk: visible_contact_surface_documents="
                f"{self.visible_contact_surface_documents}"
            ),
            (
                "risk: visible_email_like_occurrences="
                f"{self.visible_email_like_occurrences}"
            ),
            (
                "risk: visible_phone_like_occurrences="
                f"{self.visible_phone_like_occurrences}"
            ),
            (
                "risk: embedded_jpeg_occurrences="
                f"{self.embedded_jpeg_occurrences}"
            ),
            (
                "risk: embedded_jpeg_unique_payloads="
                f"{self.embedded_jpeg_unique_payloads}"
            ),
            (
                "risk: third_party_mark_occurrences="
                f"{self.third_party_mark_occurrences}"
            ),
            (
                "risk: third_party_mark_unique_payloads="
                f"{self.third_party_mark_unique_payloads}"
            ),
        )


class _VisibleTextParser(HTMLParser):
    """Collect visible text while excluding script/style/template bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.casefold() in IGNORED_TEXT_ELEMENTS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in IGNORED_TEXT_ELEMENTS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(self.parts)


def _fail(code: str) -> None:
    raise VerificationFailure(code)


def _is_plain_int(value: object) -> bool:
    return type(value) is int


def _require_mapping(value: object, expected_keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != expected_keys:
        _fail("manifest_schema_invalid")
    return value


def _require_nonnegative_int(value: object) -> int:
    if not _is_plain_int(value) or value < 0:
        _fail("manifest_schema_invalid")
    return value


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        _fail("manifest_schema_invalid")
    return value


def _read_regular_file_bounded(
    path: Path,
    *,
    max_bytes: int,
    unreadable_code: str,
) -> bytes:
    """Read one regular file while bounding pre-read and raced file sizes."""

    try:
        initial = path.lstat()
    except OSError:
        _fail(unreadable_code)
    if not stat.S_ISREG(initial.st_mode):
        _fail(unreadable_code)
    if initial.st_size > max_bytes:
        _fail("resource_limit_exceeded")

    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                _fail(unreadable_code)
            if opened.st_size > max_bytes:
                _fail("resource_limit_exceeded")
            payload = stream.read(max_bytes + 1)
    except OSError:
        _fail(unreadable_code)
    if len(payload) > max_bytes:
        _fail("resource_limit_exceeded")
    return payload


def _read_manifest(root: Path) -> Mapping[str, Any]:
    try:
        payload = _read_regular_file_bounded(
            root / MANIFEST_NAME,
            max_bytes=MAX_MANIFEST_BYTES,
            unreadable_code="manifest_unreadable",
        )
        raw = payload.decode("utf-8")
        manifest = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("manifest_unreadable")
    return _require_mapping(manifest, TOP_LEVEL_KEYS)


def _validate_inventory_schema(
    snapshot_value: object,
) -> tuple[list[Mapping[str, Any]], int]:
    snapshot = _require_mapping(snapshot_value, SNAPSHOT_KEYS)
    count = _require_nonnegative_int(snapshot["legacy_file_count"])
    total_bytes = _require_nonnegative_int(snapshot["legacy_total_bytes"])
    if total_bytes > MAX_LEGACY_TOTAL_BYTES:
        _fail("resource_limit_exceeded")
    inventory_value = snapshot["inventory"]
    if not isinstance(inventory_value, list):
        _fail("manifest_schema_invalid")

    inventory: list[Mapping[str, Any]] = []
    paths: list[str] = []
    for entry_value in inventory_value:
        entry = _require_mapping(entry_value, INVENTORY_ENTRY_KEYS)
        path_text = _require_string(entry["path"])
        path = Path(path_text)
        if (
            not path_text
            or path.is_absolute()
            or path.parent != Path(".")
            or path.name != path_text
        ):
            _fail("manifest_schema_invalid")
        file_bytes = _require_nonnegative_int(entry["bytes"])
        if file_bytes > MAX_LEGACY_FILE_BYTES:
            _fail("resource_limit_exceeded")
        digest = _require_string(entry["sha256"])
        if SHA256_PATTERN.fullmatch(digest) is None:
            _fail("manifest_schema_invalid")
        paths.append(path_text)
        inventory.append(entry)

    if (
        count != EXPECTED_LEGACY_FILE_COUNT
        or len(inventory) != EXPECTED_LEGACY_FILE_COUNT
        or paths != sorted(paths)
        or len(set(paths)) != len(paths)
        or sum(path.endswith(".html") for path in paths)
        != EXPECTED_HTML_FILE_COUNT
        or paths.count(SITE_METADATA_NAME) != 1
        or sum(int(entry["bytes"]) for entry in inventory) != total_bytes
    ):
        _fail("manifest_schema_invalid")
    return inventory, total_bytes


def _verify_inventory(
    root: Path,
    inventory: Sequence[Mapping[str, Any]],
    expected_total_bytes: int,
) -> dict[str, bytes]:
    expected_paths = {str(entry["path"]) for entry in inventory}
    try:
        discovered_html = {
            path.name
            for path in root.iterdir()
            if path.suffix.casefold() == ".html"
        }
    except OSError:
        _fail("inventory_unreadable")

    discovered_paths = discovered_html | {SITE_METADATA_NAME}
    if discovered_paths != expected_paths:
        _fail("inventory_mismatch")

    contents: dict[str, bytes] = {}
    actual_total_bytes = 0
    for entry in inventory:
        path_text = str(entry["path"])
        candidate = root / path_text
        try:
            file_stat = candidate.lstat()
            if not stat.S_ISREG(file_stat.st_mode):
                _fail("inventory_mismatch")
        except OSError:
            _fail("inventory_unreadable")
        if (
            file_stat.st_size > MAX_LEGACY_FILE_BYTES
            or actual_total_bytes + file_stat.st_size
            > MAX_LEGACY_TOTAL_BYTES
        ):
            _fail("resource_limit_exceeded")
        if file_stat.st_size != entry["bytes"]:
            _fail("size_mismatch")

        payload = _read_regular_file_bounded(
            candidate,
            max_bytes=MAX_LEGACY_FILE_BYTES,
            unreadable_code="inventory_unreadable",
        )

        size = len(payload)
        digest = hashlib.sha256(payload).hexdigest()
        if size != entry["bytes"]:
            _fail("size_mismatch")
        if digest != entry["sha256"]:
            _fail("checksum_mismatch")
        actual_total_bytes += size
        contents[path_text] = payload

    if actual_total_bytes != expected_total_bytes:
        _fail("size_mismatch")
    return contents


def _validate_generator_contract(
    contract_value: object,
    contents: Mapping[str, bytes],
) -> Mapping[str, Any]:
    contract = _require_mapping(contract_value, GENERATOR_KEYS)
    metadata_path = _require_string(contract["metadata_path"])
    app_version = _require_string(contract["app_version"])
    if type(contract["is_trial"]) is not bool:
        _fail("manifest_schema_invalid")
    unpublished = _require_mapping(contract["unpublished"], UNPUBLISHED_KEYS)
    if (
        unpublished["date_published"] is not None
        or unpublished["public_url"] is not None
        or unpublished["custom_domain_url"] is not None
        or not _is_plain_int(unpublished["server_site_id"])
    ):
        _fail("manifest_schema_invalid")
    if (
        metadata_path != SITE_METADATA_NAME
        or app_version != "4.14.1"
        or contract["is_trial"] is not True
        or unpublished["server_site_id"] != 0
    ):
        _fail("manifest_schema_invalid")

    try:
        metadata = json.loads(contents[metadata_path].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("generator_metadata_invalid")
    if not isinstance(metadata, dict):
        _fail("generator_metadata_invalid")
    required_metadata_keys = {
        "appVersion",
        "isTrial",
        "datePublished",
        "publicUrl",
        "customDomainUrl",
        "serverSiteId",
        "settings",
    }
    if not required_metadata_keys.issubset(metadata):
        _fail("generator_metadata_mismatch")

    actual = {
        "app_version": metadata.get("appVersion"),
        "is_trial": metadata.get("isTrial"),
        "date_published": metadata.get("datePublished"),
        "public_url": metadata.get("publicUrl"),
        "custom_domain_url": metadata.get("customDomainUrl"),
        "server_site_id": metadata.get("serverSiteId"),
    }
    expected = {
        "app_version": app_version,
        "is_trial": contract["is_trial"],
        **unpublished,
    }
    if actual != expected:
        _fail("generator_metadata_mismatch")
    return metadata


def _compile_data_uri_pattern(mime_type: str) -> re.Pattern[bytes]:
    prefix = re.escape(f"data:{mime_type};base64,".encode("ascii"))
    return re.compile(prefix + rb"([A-Za-z0-9+/]+={0,2})(?![A-Za-z0-9+/=])")


def _collect_payloads(
    sources: Mapping[str, bytes],
    mime_type: str,
) -> dict[str, tuple[int, Counter[str]]]:
    pattern = _compile_data_uri_pattern(mime_type)
    payloads: dict[str, tuple[int, Counter[str]]] = {}
    for source_name, source in sources.items():
        for match in pattern.finditer(source):
            encoded = match.group(1)
            if len(encoded) > MAX_ENCODED_MEDIA_BYTES:
                _fail("resource_limit_exceeded")
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                _fail("embedded_media_invalid")
            if len(decoded) > MAX_DECODED_MEDIA_BYTES:
                _fail("resource_limit_exceeded")
            digest = hashlib.sha256(decoded).hexdigest()
            size = len(decoded)
            if digest in payloads:
                known_size, locations = payloads[digest]
                if known_size != size:
                    _fail("embedded_media_invalid")
            else:
                locations = Counter()
                payloads[digest] = (size, locations)
            locations[source_name] += 1
    return payloads


def _validate_jpeg_contract(
    contract_value: object,
    contents: Mapping[str, bytes],
    metadata: Mapping[str, Any],
) -> tuple[int, int]:
    contract = _require_mapping(contract_value, JPEG_CONTRACT_KEYS)
    source_path = _require_string(contract["source_path"])
    json_field = _require_string(contract["json_field"])
    mime_type = _require_string(contract["mime_type"])
    total_occurrences = _require_nonnegative_int(contract["total_occurrences"])
    payload_values = contract["unique_payloads"]
    if not isinstance(payload_values, list):
        _fail("manifest_schema_invalid")
    if (
        source_path != SITE_METADATA_NAME
        or json_field != "settings"
        or mime_type != "image/jpeg"
        or total_occurrences != EXPECTED_JPEG_OCCURRENCES
        or len(payload_values) != EXPECTED_JPEG_UNIQUE_PAYLOADS
    ):
        _fail("manifest_schema_invalid")

    expected: dict[str, tuple[int, Counter[str]]] = {}
    ordered_hashes: list[str] = []
    for payload_value in payload_values:
        payload = _require_mapping(payload_value, JPEG_PAYLOAD_KEYS)
        digest = _require_string(payload["sha256"])
        if SHA256_PATTERN.fullmatch(digest) is None:
            _fail("manifest_schema_invalid")
        decoded_bytes = _require_nonnegative_int(payload["decoded_bytes"])
        locations_value = payload["locations"]
        if not isinstance(locations_value, list) or not locations_value:
            _fail("manifest_schema_invalid")
        locations: Counter[str] = Counter()
        location_paths: list[str] = []
        for location_value in locations_value:
            location = _require_mapping(location_value, JPEG_LOCATION_KEYS)
            path = _require_string(location["path"])
            occurrences = _require_nonnegative_int(location["occurrences"])
            if path != source_path or occurrences == 0:
                _fail("manifest_schema_invalid")
            locations[path] += occurrences
            location_paths.append(path)
        if location_paths != sorted(location_paths) or len(set(location_paths)) != len(
            location_paths
        ):
            _fail("manifest_schema_invalid")
        ordered_hashes.append(digest)
        if digest in expected:
            _fail("manifest_schema_invalid")
        expected[digest] = (decoded_bytes, locations)

    if ordered_hashes != sorted(ordered_hashes):
        _fail("manifest_schema_invalid")
    expected_occurrences = sum(
        sum(locations.values()) for _, locations in expected.values()
    )
    if expected_occurrences != total_occurrences:
        _fail("manifest_schema_invalid")

    settings = metadata.get(json_field)
    if not isinstance(settings, str):
        _fail("generator_metadata_mismatch")
    actual = _collect_payloads(contents, mime_type)
    settings_only = _collect_payloads(
        {source_path: settings.encode("utf-8")},
        mime_type,
    )
    if actual != settings_only or actual != expected:
        _fail("embedded_media_mismatch")

    actual_occurrences = sum(
        sum(locations.values()) for _, locations in actual.values()
    )
    actual_unique = len(actual)
    if (
        actual_occurrences != EXPECTED_JPEG_OCCURRENCES
        or actual_unique != EXPECTED_JPEG_UNIQUE_PAYLOADS
    ):
        _fail("embedded_media_mismatch")
    return actual_occurrences, actual_unique


def _visible_contact_counts(
    html_sources: Iterable[bytes],
) -> tuple[int, int, int]:
    email_count = 0
    phone_count = 0
    contact_documents = 0
    for source in html_sources:
        try:
            text_source = source.decode("utf-8")
        except UnicodeDecodeError:
            _fail("legacy_text_invalid")
        parser = _VisibleTextParser()
        try:
            parser.feed(text_source)
            parser.close()
        except Exception:
            _fail("legacy_text_invalid")
        visible_text = parser.text()
        document_email_count = len(EMAIL_PATTERN.findall(visible_text))
        document_phone_count = len(PHONE_LIKE_PATTERN.findall(visible_text))
        email_count += document_email_count
        phone_count += document_phone_count
        contact_documents += int(
            document_email_count > 0 or document_phone_count > 0
        )
    return contact_documents, email_count, phone_count


def _validate_risk_contract(
    contract_value: object,
    contents: Mapping[str, bytes],
    jpeg_counts: tuple[int, int],
) -> dict[str, int]:
    contract = _require_mapping(contract_value, RISK_KEYS)
    expected: dict[str, int] = {}
    for key in sorted(RISK_KEYS):
        expected[key] = _require_nonnegative_int(contract[key])

    html_sources = {
        path: payload
        for path, payload in contents.items()
        if path.endswith(".html")
    }
    contact_documents, email_count, phone_count = _visible_contact_counts(
        html_sources.values()
    )
    third_party_payloads = _collect_payloads(
        html_sources,
        "image/svg+xml",
    )
    third_party_occurrences = sum(
        sum(locations.values())
        for _, locations in third_party_payloads.values()
    )
    third_party_unique = len(third_party_payloads)

    actual = {
        "legacy_prose_documents": len(html_sources),
        "visible_contact_surface_documents": contact_documents,
        "visible_email_like_occurrences": email_count,
        "visible_phone_like_occurrences": phone_count,
        "embedded_jpeg_occurrences": jpeg_counts[0],
        "embedded_jpeg_unique_payloads": jpeg_counts[1],
        "third_party_mark_occurrences": third_party_occurrences,
        "third_party_mark_unique_payloads": third_party_unique,
    }
    if (
        third_party_occurrences != EXPECTED_THIRD_PARTY_MARK_OCCURRENCES
        or third_party_unique != EXPECTED_THIRD_PARTY_MARK_UNIQUE_PAYLOADS
    ):
        _fail("third_party_mark_mismatch")
    if actual != expected:
        _fail("redacted_risk_mismatch")
    return actual


def verify(root: Path) -> VerificationReport:
    """Verify one repository root and return aggregate, redacted evidence."""

    root = root.resolve()
    manifest = _read_manifest(root)
    if not _is_plain_int(manifest["schema_version"]) or manifest["schema_version"] != 1:
        _fail("manifest_schema_invalid")

    inventory, expected_total_bytes = _validate_inventory_schema(
        manifest["snapshot"]
    )
    contents = _verify_inventory(root, inventory, expected_total_bytes)
    metadata = _validate_generator_contract(
        manifest["generator_contract"],
        contents,
    )
    jpeg_counts = _validate_jpeg_contract(
        manifest["embedded_jpeg_contract"],
        contents,
        metadata,
    )
    risk_counts = _validate_risk_contract(
        manifest["redacted_risk_contract"],
        contents,
        jpeg_counts,
    )

    return VerificationReport(
        legacy_files=len(contents),
        legacy_bytes=sum(len(payload) for payload in contents.values()),
        **risk_counts,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run against the repository containing this tool."""

    if argv is None:
        argv = sys.argv[1:]
    if list(argv) not in ([], ["--check"]):
        print(
            "TCTravel legacy attestation: FAIL code=unsupported_arguments",
            file=sys.stderr,
        )
        return 2

    root = Path(__file__).resolve().parents[1]
    try:
        report = verify(root)
    except VerificationFailure as error:
        print(
            f"TCTravel legacy attestation: FAIL code={error.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "TCTravel legacy attestation: FAIL code=internal_error",
            file=sys.stderr,
        )
        return 1

    print("\n".join(report.output_lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
