from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tctravel import decode_itinerary_json
from tctravel.analysis import (
    analyze_interval_feasibility,
    canonical_analysis_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
ROBUST_EXAMPLE = ROOT / "examples" / "synthetic_connection.v1.json"
TIGHT_EXAMPLE = ROOT / "examples" / "synthetic_tight_connection.v1.json"


def _expected_stdout(path: Path) -> bytes:
    payload = path.read_bytes()
    return canonical_analysis_bytes(
        analyze_interval_feasibility(decode_itinerary_json(payload))
    ) + b"\n"


class PackagingTests(unittest.TestCase):
    def test_built_wheel_installs_and_runs_outside_source_tree(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="tctravel-packaging-",
            dir=ROOT,
        ) as raw_directory:
            sandbox = Path(raw_directory)
            source = sandbox / "source"
            distribution = sandbox / "dist"
            environment = sandbox / "installed"
            outside = sandbox / "outside"
            cache = sandbox / "pip-cache"
            home = sandbox / "home"
            temporary = sandbox / "tmp"
            for directory in (
                source,
                distribution,
                outside,
                cache,
                home,
                temporary,
            ):
                directory.mkdir()

            shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
            shutil.copytree(
                ROOT / "tctravel",
                source / "tctravel",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            robust_fixture = outside / "robust.json"
            tight_fixture = outside / "tight.json"
            shutil.copy2(ROBUST_EXAMPLE, robust_fixture)
            shutil.copy2(TIGHT_EXAMPLE, tight_fixture)

            clean_environment = os.environ.copy()
            clean_environment.pop("PYTHONPATH", None)
            clean_environment.update(
                {
                    "HOME": str(home),
                    "PIP_CACHE_DIR": str(cache),
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    "PIP_NO_INDEX": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(temporary),
                }
            )

            built = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-build-isolation",
                    "--no-deps",
                    "--wheel-dir",
                    str(distribution),
                    str(source),
                ],
                cwd=outside,
                env=clean_environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                built.returncode,
                0,
                built.stderr.decode("utf-8", errors="replace"),
            )
            wheels = list(distribution.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            wheel = wheels[0]

            with zipfile.ZipFile(wheel) as archive:
                members = archive.namelist()
                self.assertIn("tctravel/analysis.py", members)
                self.assertIn("tctravel/cli.py", members)
                self.assertIn("tctravel/__main__.py", members)
                entry_points = next(
                    member
                    for member in members
                    if member.endswith(".dist-info/entry_points.txt")
                )
                entry_point_text = archive.read(entry_points).decode("utf-8")
                self.assertIn(
                    "tctravel-analyze = tctravel.cli:main",
                    entry_point_text,
                )
                self.assertFalse(
                    any(member.endswith(".html") for member in members)
                )
                self.assertNotIn("site.json", members)
                self.assertFalse(
                    any(member.startswith("tests/") for member in members)
                )

            created = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "venv",
                    "--system-site-packages",
                    str(environment),
                ],
                cwd=outside,
                env=clean_environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                created.returncode,
                0,
                created.stderr.decode("utf-8", errors="replace"),
            )
            installed_python = environment / "bin" / "python"
            installed = subprocess.run(
                [
                    str(installed_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-index",
                    str(wheel),
                ],
                cwd=outside,
                env=clean_environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                installed.returncode,
                0,
                installed.stderr.decode("utf-8", errors="replace"),
            )

            executable = environment / "bin" / "tctravel-analyze"
            robust = subprocess.run(
                [str(executable), str(robust_fixture)],
                cwd=outside,
                env=clean_environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(robust.returncode, 0)
            self.assertEqual(robust.stdout, _expected_stdout(ROBUST_EXAMPLE))
            self.assertEqual(robust.stderr, b"")

            tight = subprocess.run(
                [str(executable), str(tight_fixture)],
                cwd=outside,
                env=clean_environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(tight.returncode, 1)
            self.assertEqual(tight.stdout, _expected_stdout(TIGHT_EXAMPLE))
            self.assertEqual(tight.stderr, b"")

            module = subprocess.run(
                [str(installed_python), "-m", "tctravel", "-"],
                cwd=outside,
                env=clean_environment,
                input=ROBUST_EXAMPLE.read_bytes(),
                capture_output=True,
                check=False,
            )
            self.assertEqual(module.returncode, 0)
            self.assertEqual(module.stdout, robust.stdout)
            self.assertEqual(module.stderr, b"")


if __name__ == "__main__":
    unittest.main()
