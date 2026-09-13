import os
from pathlib import Path
import re
import tarfile
import tomllib
import unittest
from zipfile import ZipFile

import worldfuzz
from worldfuzz.platform.mcp import handle


ROOT = Path(__file__).parents[1]
VERSION = "0.4.0rc5"


class ReleaseSourceTests(unittest.TestCase):
    def test_version_and_container_pin_are_consistent(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["version"], VERSION)
        self.assertEqual(worldfuzz.__version__, VERSION)
        dockerfile = (ROOT / "deployment" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(f"worldfuzz-{VERSION}-py3-none-any.whl", dockerfile)
        self.assertIn("COPY ${WORLDFUZZ_WHEEL} /tmp/\n", dockerfile)
        self.assertNotIn("/tmp/worldfuzz.whl", dockerfile)
        self.assertIn("installed-wheel.sha256", dockerfile)
        # Only the parent directory and exact candidate are re-included. A broad
        # dist/** or source exception would expose unrelated private artifacts.
        rules = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
        self.assertEqual(rules, ["**", "!dist/", f"!dist/worldfuzz-{VERSION}-py3-none-any.whl"])
        compose = (ROOT / "deployment" / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("dockerfile: deployment/Dockerfile", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("WORLDFUZZ_PRIVATE_DIR", compose)
        initialized = handle(None, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(initialized["result"]["serverInfo"]["version"], VERSION)

    def test_packaged_and_deployment_migrations_match(self):
        for name in ("001_catalog.sql", "002_m1_catalog.sql"):
            self.assertEqual(
                (ROOT / "worldfuzz" / "sql" / "postgresql" / name).read_bytes(),
                (ROOT / "deployment" / "postgresql" / name).read_bytes(),
            )


class BuiltReleaseTests(unittest.TestCase):
    REQUIRED_WHEEL = {
        "worldfuzz/__init__.py",
        "worldfuzz/entities/schemas.py",
        "worldfuzz/platform/contracts.py",
        "worldfuzz/ui/platform.html",
        "worldfuzz/ui/platform.mjs",
        "worldfuzz/sql/postgresql/001_catalog.sql",
        "worldfuzz/sql/postgresql/002_m1_catalog.sql",
        "worldfuzz/benchmark_data/Berlin_0_256.map",
        "worldfuzz/benchmark_data/selection.json",
        "worldfuzz/benchmark_data/README.md",
        "worldfuzz/vendor/PYTHONROBOTICS_LICENSE",
    }
    REQUIRED_SHARED = {
        "share/worldfuzz/contracts/openapi-v1.0.0.json",
        "share/worldfuzz/contracts/operation-examples-v1.0.0.json",
        "share/worldfuzz/deployment/compose.yaml",
        "share/worldfuzz/deployment/postgresql/001_catalog.sql",
        "share/worldfuzz/examples/published-street-cases-20260910/report.json",
        "share/worldfuzz/examples/m6-acceptance-20260911/receipt.json",
        "share/worldfuzz/licenses/PYTHONROBOTICS_LICENSE",
    }
    FORBIDDEN = re.compile(
        r"(^|/)(credentials\.json|\.env(?:\..*)?|.*connection(?:-request|\.enc)?\.json)$"
        r"|\.(?:sqlite3?|db|hdf5?|dpapi|pem)(?:$|-)", re.IGNORECASE
    )

    def setUp(self):
        wheel = os.environ.get("WORLDFUZZ_RELEASE_WHEEL")
        sdist = os.environ.get("WORLDFUZZ_RELEASE_SDIST")
        if not wheel or not sdist:
            self.skipTest("set WORLDFUZZ_RELEASE_WHEEL and WORLDFUZZ_RELEASE_SDIST")
        self.wheel, self.sdist = Path(wheel), Path(sdist)

    def test_wheel_inventory_and_exclusions(self):
        with ZipFile(self.wheel) as archive:
            names = set(archive.namelist())
            self.assertTrue(self.REQUIRED_WHEEL <= names)
            for suffix in self.REQUIRED_SHARED:
                self.assertTrue(any(name.endswith(suffix) for name in names), suffix)
            self.assertTrue(any(name.endswith(".dist-info/licenses/LICENSE") for name in names))
            self.assertFalse([name for name in names if self.FORBIDDEN.search(name)])
            packaged = archive.read("worldfuzz/__init__.py").decode("utf-8")
            self.assertIn(f'__version__ = "{VERSION}"', packaged)

    def test_sdist_inventory_and_exclusions(self):
        with tarfile.open(self.sdist, "r:gz") as archive:
            names = archive.getnames()
            relative = {name.split("/", 1)[1] for name in names if "/" in name}
            required = self.REQUIRED_WHEEL | {
                "LICENSE",
                "README.md",
                "docs/RELEASE_INSTALLATION.md",
                "docs/contracts/openapi-v1.0.0.json",
                "deployment/Dockerfile",
                ".dockerignore",
                "deployment/compose.yaml",
                "examples/published-street-cases-20260910/report.json",
            }
            self.assertTrue(required <= relative, sorted(required - relative))
            self.assertFalse([name for name in relative if self.FORBIDDEN.search(name)])


if __name__ == "__main__":
    unittest.main()
