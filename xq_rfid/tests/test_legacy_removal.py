from pathlib import Path
import ast
import importlib.util
import inspect
import unittest

from odoo.modules.migration import VALID_MIGRATE_PARAMS

ADDON = Path(__file__).resolve().parents[1]
MIGRATION = ADDON / "migrations/18.0.2.0/pre-disable-uhf-reader18.py"
FORBIDDEN_ROOTS = [
    ADDON / "models",
    ADDON / "wizard",
    ADDON / "views",
    ADDON / "security",
    ADDON / "static",
]
TOKENS = ("UHFReader18", "uhf_reader18", "uhf.reader18")
EXPECTED_TRANSITIONAL_OFFENDERS = set()


def _legacy_offenders():
    offenders = set()
    for root in FORBIDDEN_ROOTS:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".xml", ".csv", ".js"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if any(token in text for token in TOKENS):
                    offenders.add(str(path.relative_to(ADDON)))
    return offenders


def _load_migration():
    spec = importlib.util.spec_from_file_location("pre_disable_uhf_reader18", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, table="rfid_device_config", columns=()):
        self.table = table
        self.columns = columns
        self.calls = []
        self._result = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if "to_regclass" in query:
            self._result = [(self.table,)]
        elif "information_schema.columns" in query:
            self._result = [(name,) for name in self.columns]
        else:
            self._result = []

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result


class TestLegacyRemoval(unittest.TestCase):
    def test_transitional_legacy_offenders_are_explicit(self):
        self.assertEqual(_legacy_offenders(), EXPECTED_TRANSITIONAL_OFFENDERS)

    def test_runtime_files_do_not_reference_legacy_driver(self):
        self.assertEqual(
            _legacy_offenders(),
            set(),
            "Tasks 2-3 must remove the explicitly tracked transitional offenders",
        )

    def test_manifest_does_not_load_legacy_wizard(self):
        manifest = ast.literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertNotIn("wizard/uhf_reader18_wizard_views.xml", manifest["data"])

    def test_acl_does_not_reference_deleted_models(self):
        acl = (ADDON / "security/ir.model.access.csv").read_text(encoding="utf-8")
        self.assertNotIn("model_uhf_reader18_service", acl)
        self.assertNotIn("model_uhf_reader18_config_wizard", acl)
        self.assertNotIn("model_uhf_reader18_demo_wizard", acl)

    def test_manifest_references_existing_files(self):
        manifest = ast.literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))
        missing = [name for name in manifest.get("data", []) if not (ADDON / name).is_file()]
        self.assertEqual(missing, [])


class TestMigration(unittest.TestCase):
    def test_signature_is_accepted_by_installed_odoo_loader(self):
        signature = inspect.signature(_load_migration().migrate)
        self.assertIn(tuple(signature.parameters), VALID_MIGRATE_PARAMS)
        self.assertTrue(
            all(
                parameter.kind
                in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                for parameter in signature.parameters.values()
            )
        )

    def test_missing_table_is_noop(self):
        cursor = FakeCursor(table=None)
        _load_migration().migrate(cursor, "18.0.1.0.0")
        self.assertEqual(len(cursor.calls), 1)

    def test_missing_required_column_is_noop(self):
        cursor = FakeCursor(columns=("device_type",))
        _load_migration().migrate(cursor, "18.0.1.0.0")
        self.assertEqual(len(cursor.calls), 2)

    def test_full_schema_updates_only_legacy_type_with_parameters(self):
        cursor = FakeCursor(
            columns=("active", "device_type", "connection_status", "error_message")
        )
        _load_migration().migrate(cursor, "18.0.1.0.0")
        query, params = cursor.calls[-1]
        self.assertIn("active = FALSE", query)
        self.assertIn("device_type = %s", query)
        self.assertIn("connection_status = %s", query)
        self.assertIn("error_message = %s", query)
        self.assertIn("WHERE device_type = %s", query)
        self.assertEqual(
            params,
            [
                "legacy_disabled",
                "error",
                "旧 UHFReader18 配置已停用；必须按 SI120X1 实机接口重新配置并验证。",
                "uhf_reader18",
            ],
        )


class TestFailClosedSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device_source = (ADDON / "models/rfid_device.py").read_text(encoding="utf-8")
        cls.point_source = (ADDON / "models/quality_point.py").read_text(encoding="utf-8")
        cls.quality_source = (ADDON / "models/quality_check.py").read_text(encoding="utf-8")
        cls.wizard_source = (ADDON / "wizard/rfid_read_wizard.py").read_text(
            encoding="utf-8"
        )

    @staticmethod
    def _method_source(source, class_name, method_name):
        tree = ast.parse(source)
        model = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in model.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        )
        return ast.get_source_segment(source, method)

    def test_business_paths_delegate_to_canonical_device_operations(self):
        label_source = self._method_source(
            self.quality_source, "QualityCheck", "_write_to_rfid_device"
        )
        write_source = self._method_source(
            self.quality_source, "QualityCheck", "_execute_rfid_write"
        )
        read_source = self._method_source(
            self.wizard_source, "RfidReadWizard", "action_read_rfid"
        )
        self.assertIn("device.write_and_verify(", label_source)
        self.assertIn("device.write_and_verify(", write_source)
        self.assertIn("self.device_id.read_memory(", read_source)
        for source in (label_source, write_source, read_source):
            self.assertNotIn("_ensure_operational", source)
            self.assertNotIn("_raise_adapter_not_configured", source)

    def test_device_operations_own_the_fail_closed_guards(self):
        for method_name in ("write_and_verify", "read_memory"):
            with self.subTest(method=method_name):
                source = self._method_source(
                    self.device_source, "RfidDeviceConfig", method_name
                )
                self.assertIn("self._ensure_rfid_manager()", source)
                self.assertIn("self._ensure_operational()", source)
                self.assertIn("self._raise_adapter_not_configured()", source)

    def test_connection_probe_does_not_require_validated_state(self):
        source = self._method_source(
            self.device_source, "RfidDeviceConfig", "action_test_connection"
        )
        self.assertIn("self._ensure_probe_ready()", source)
        self.assertNotIn("self._ensure_operational()", source)

    def test_selectable_policy_is_canonical_and_company_aware(self):
        domain_source = self._method_source(
            self.device_source, "RfidDeviceConfig", "_selectable_domain"
        )
        self.assertIn("company", domain_source)
        for value in ("si120x1", "active", "validated", "company_id"):
            self.assertIn(value, domain_source)
        self.assertIn("_find_selectable", self.point_source)
        self.assertIn("default_company_id", self.point_source)
        self.assertIn("default_test_type_id", self.point_source)
        self.assertIn("_find_selectable", self.wizard_source)
        self.assertIn("company_id", self.wizard_source)

    def test_quality_pass_is_recordset_safe_and_required_paths_fail_closed(self):
        source = self._method_source(self.quality_source, "QualityCheck", "do_pass")
        prepare_source = self._method_source(
            self.quality_source, "QualityCheck", "_prepare_rfid_before_pass"
        )
        self.assertIn("for check in self", source)
        self.assertLess(source.index("_prepare_rfid_before_pass"), source.index("super("))
        self.assertIn("super(QualityCheck, check).do_pass()", source)
        self.assertIn("hardware_required", prepare_source)
        self.assertIn("lot_producing_id", prepare_source)
        self.assertIn("请先设置成品批次", prepare_source)
        self.assertNotIn("_logger.warning", source)

    def test_diagnostic_actions_require_manager_before_returning_data(self):
        for method_name in ("action_view_write_logs", "action_view_read_logs"):
            with self.subTest(method=method_name):
                source = self._method_source(
                    self.device_source, "RfidDeviceConfig", method_name
                )
                self.assertIn("self._ensure_rfid_manager()", source)
                self.assertLess(source.index("_ensure_rfid_manager"), source.index("return"))


class TestLegacyDisabledRestrictions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ADDON / "models/rfid_device.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.model = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RfidDeviceConfig"
        )

    def _method_source(self, name):
        method = next(
            node
            for node in self.model.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        )
        return ast.get_source_segment(self.source, method)

    def _validator(self):
        validator = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_validate_device_type"
        )

        class FakeUserError(Exception):
            pass

        namespace = {"UserError": FakeUserError, "_": lambda message: message}
        module = ast.fix_missing_locations(ast.Module(body=[validator], type_ignores=[]))
        exec(compile(module, "rfid_device.py", "exec"), namespace)
        return namespace["_validate_device_type"], FakeUserError

    def test_validator_rejects_only_legacy_disabled(self):
        validator, error = self._validator()
        validator(None)
        validator("simulation")
        validator("si120x1")
        with self.assertRaises(error):
            validator("legacy_disabled")

    def test_create_validates_effective_type_before_super(self):
        source = self._method_source("create")
        self.assertIn('self.env.context.get("default_device_type")', source)
        self.assertIn('vals.get("device_type", default_device_type)', source)
        self.assertLess(source.index("_validate_device_type"), source.index("super().create"))

    def test_write_validates_requested_type_before_super(self):
        source = self._method_source("write")
        self.assertIn('_validate_device_type(vals.get("device_type"))', source)
        self.assertLess(source.index("_validate_device_type"), source.index("super().write"))


if __name__ == "__main__":
    unittest.main()
