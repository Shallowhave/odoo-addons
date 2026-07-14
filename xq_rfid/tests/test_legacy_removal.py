from pathlib import Path
import ast
import unittest

ADDON = Path(__file__).resolve().parents[1]
ALLOWED = {
    ADDON / "migrations/18.0.2.0/pre-disable-uhf-reader18.py",
}
FORBIDDEN_ROOTS = [
    ADDON / "models",
    ADDON / "wizard",
    ADDON / "views",
    ADDON / "security",
    ADDON / "static",
]
TOKENS = ("UHFReader18", "uhf_reader18", "uhf.reader18")


class TestLegacyRemoval(unittest.TestCase):
    def test_runtime_files_do_not_reference_legacy_driver(self):
        offenders = []
        for root in FORBIDDEN_ROOTS:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in {".py", ".xml", ".csv", ".js"}:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    if any(token in text for token in TOKENS):
                        offenders.append(str(path.relative_to(ADDON)))
        self.assertEqual(offenders, [])

    def test_manifest_references_existing_files(self):
        manifest = ast.literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))
        missing = [name for name in manifest.get("data", []) if not (ADDON / name).is_file()]
        self.assertEqual(missing, [])
