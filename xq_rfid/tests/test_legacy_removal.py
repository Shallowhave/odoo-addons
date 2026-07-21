from pathlib import Path
import ast
import importlib.util
import inspect
import unittest

from odoo.modules.migration import VALID_MIGRATE_PARAMS

ADDON = Path(__file__).resolve().parents[1]
MIGRATION = ADDON / "migrations/18.0.2.0/pre-disable-uhf-reader18.py"
GROUP_MEMBERSHIP_MIGRATION = (
    ADDON / "migrations/18.0.2.0/pre-clean-rfid-user-type-memberships.py"
)
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


def _load_group_membership_migration():
    spec = importlib.util.spec_from_file_location(
        "pre_clean_rfid_user_type_memberships",
        GROUP_MEMBERSHIP_MIGRATION,
    )
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

    def test_cron_data_uses_only_odoo_18_fields(self):
        cron_data = (ADDON / "data/rfid_operation_cron.xml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('name="numbercall"', cron_data)
        self.assertNotIn('name="doall"', cron_data)

    def test_rfid_tag_extension_targets_existing_form_architecture(self):
        from lxml import etree

        base_root = etree.parse(str(ADDON / "views/rfid_tag_views.xml"))
        extension_root = etree.parse(
            str(ADDON / "views/rfid_tag_extension_views.xml")
        )
        extension = extension_root.xpath(
            "/odoo/record[@id='rfid_tag_view_form_inherit_physical']"
        )[0]
        inherit_ref = extension.xpath("./field[@name='inherit_id']")[0].get("ref")
        self.assertEqual(inherit_ref, "xq_rfid.rfid_tag_form_view")

        expression = extension.xpath("./field[@name='arch']/xpath")[0].get("expr")
        form_arch = base_root.xpath(
            "/odoo/record[@id='rfid_tag_form_view']/field[@name='arch']/form"
        )[0]
        self.assertTrue(form_arch.xpath(expression))


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


class TestGroupMembershipMigration(unittest.TestCase):
    def test_signature_is_accepted_by_installed_odoo_loader(self):
        signature = inspect.signature(_load_group_membership_migration().migrate)
        self.assertIn(tuple(signature.parameters), VALID_MIGRATE_PARAMS)

    def test_portal_and_public_users_lose_only_rfid_groups(self):
        cursor = FakeCursor()

        _load_group_membership_migration().migrate(cursor, "18.0.1.0.0")

        query, params = cursor.calls[-1]
        normalized_query = " ".join(query.split())
        self.assertIn("DELETE FROM res_groups_users_rel", normalized_query)
        self.assertIn("rfid_membership.uid = user_type_membership.uid", normalized_query)
        self.assertIn("rfid_group.name = ANY(%s)", normalized_query)
        self.assertIn("user_type_group.name = ANY(%s)", normalized_query)
        self.assertEqual(
            params,
            (
                ["group_rfid_user", "group_rfid_manager"],
                ["group_portal", "group_public"],
            ),
        )


class TestFailClosedSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device_source = (ADDON / "models/rfid_device.py").read_text(encoding="utf-8")
        cls.point_source = (ADDON / "models/quality_point.py").read_text(encoding="utf-8")
        cls.point_view_source = (ADDON / "views/quality_point_views.xml").read_text(
            encoding="utf-8"
        )
        cls.security_source = (ADDON / "security/security.xml").read_text(
            encoding="utf-8"
        )
        cls.device_view_source = (ADDON / "views/rfid_device_views.xml").read_text(
            encoding="utf-8"
        )
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
        self.assertIn("_selectable_domain", self.point_source)
        self.assertIn("default_company_id", self.point_source)
        self.assertIn("default_test_type_id", self.point_source)
        self.assertIn("default_rfid_device_id", self.point_source)
        self.assertIn("_find_selectable", self.wizard_source)
        self.assertIn("company_id", self.wizard_source)

    def test_quality_pass_has_pure_recordset_plan_before_execution(self):
        source = self._method_source(self.quality_source, "QualityCheck", "do_pass")
        plan_source = self._method_source(
            self.quality_source, "QualityCheck", "_plan_rfid_before_pass"
        )
        execute_source = self._method_source(
            self.quality_source, "QualityCheck", "_execute_rfid_pass_plan"
        )
        self.assertIn("plans = [", source)
        self.assertLess(source.index("_plan_rfid_before_pass"), source.index("_execute_rfid_pass_plan"))
        self.assertLess(source.index("_execute_rfid_pass_plan"), source.index("super("))
        self.assertIn("super(QualityCheck, check).do_pass()", source)
        for forbidden in (
            "generate_rfid_for_lot",
            "write_and_verify",
            "self.rfid_tag_id =",
            ".write(",
            ".create(",
        ):
            self.assertNotIn(forbidden, plan_source)
        self.assertIn("hardware_required", plan_source)
        self.assertIn("lot_producing_id", plan_source)
        self.assertIn("请先设置成品批次", plan_source)
        self.assertIn("generate_rfid_for_lot", execute_source)
        self.assertNotIn("_logger.warning", source)

    def test_quality_point_view_configures_required_rfid_labels(self):
        self.assertIn('name="rfid_device_required"', self.point_view_source)
        self.assertIn("test_type != 'rfid_label'", self.point_view_source)
        self.assertIn("test_type != 'rfid_write'", self.point_view_source)
        self.assertIn("not rfid_device_required", self.point_view_source)

    def test_device_company_rule_uses_allowed_company_context(self):
        self.assertIn('id="rfid_device_company_rule"', self.security_source)
        self.assertIn("model_rfid_device_config", self.security_source)
        self.assertIn("[('company_id', 'in', company_ids)]", self.security_source)
        self.assertIn("xq_rfid.group_rfid_user", self.security_source)

    def test_device_company_is_visible_in_form_and_list_views(self):
        from lxml import etree

        root = etree.fromstring(self.device_view_source.encode())
        for view_id in (
            "rfid_device_config_form_view",
            "rfid_device_config_tree_view",
        ):
            with self.subTest(view=view_id):
                fields = root.xpath(
                    f'./record[@id="{view_id}"]/field[@name="arch"]'
                    '//field[@name="company_id"]'
                )
                self.assertEqual(len(fields), 1)
                self.assertNotIn("groups", fields[0].attrib)

    def test_quality_point_has_explicit_device_company_constraint(self):
        self.assertIn("check_company=True", self.point_source)
        self.assertIn(
            '@api.constrains("company_id", "rfid_device_id")',
            self.point_source,
        )
        constraint_source = self._method_source(
            self.point_source, "QualityPoint", "_check_rfid_device_company"
        )
        self.assertIn("rfid_device_id.company_id != point.company_id", constraint_source)
        self.assertIn("ValidationError", constraint_source)

    def test_public_compatibility_methods_require_manager_before_returning_data(self):
        for method_name in (
            "write_rfid_tag",
            "read_rfid_tag",
            "verify_rfid_tag",
            "erase_rfid_tag",
            "get_device_status",
        ):
            with self.subTest(method=method_name):
                source = self._method_source(
                    self.device_source, "RfidDeviceService", method_name
                )
                self.assertIn("self._ensure_rfid_manager()", source)
                self.assertLess(source.index("_ensure_rfid_manager"), source.index("return"))

    def test_existing_label_tag_is_validated_before_hardware_write(self):
        prepare_source = self._method_source(
            self.quality_source, "QualityCheck", "_plan_rfid_before_pass"
        )
        write_source = self._method_source(
            self.quality_source, "QualityCheck", "_write_to_rfid_device"
        )
        self.assertIn("_ensure_rfid_tag_matches_finished_lot", prepare_source)
        execute_source = self._method_source(
            self.quality_source, "QualityCheck", "_execute_rfid_pass_plan"
        )
        self.assertIn("_write_to_rfid_device", execute_source)
        self.assertIn("finished_lot.name", write_source)
        self.assertIn("product = self.production_id.product_id", write_source)
        self.assertNotIn("rfid_tag.stock_prod_lot_id.name", write_source)
        self.assertNotIn("self.product_id.default_code", write_source)
        self.assertNotIn("self.product_id.name", write_source)

    def test_deferred_transaction_fixtures_use_real_manager_and_valid_qc_structure(self):
        test_source = (ADDON / "tests/test_device_fail_closed.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("cls.env = cls.setup_env(user=cls.manager)", test_source)
        self.assertIn('"xq_rfid.group_rfid_manager"', test_source)
        self.assertIn('"base.group_multi_company"', test_source)
        self.assertIn('"picking_type_ids"', test_source)
        self.assertIn("self.quality_team.id", test_source)
        self.assertIn("_ensure_mrp_picking_type", test_source)
        self.assertIn("self.assertTrue(picking_type)", test_source)
        self.assertIn('with_user(self.manager)', test_source)
        self.assertIn("_set_tag_fixture_column", test_source)
        self.assertNotIn('quality.alert.team"].search([], limit=1)', test_source)

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

    def test_create_validates_requested_company_before_super(self):
        source = self._method_source("create")
        self.assertIn("self._validate_allowed_company(", source)
        self.assertIn("default_company_id", source)
        self.assertLess(
            source.index("_validate_allowed_company"),
            source.index("super().create"),
        )

    def test_write_validates_requested_company_before_super(self):
        source = self._method_source("write")
        self.assertIn(
            'self._validate_allowed_company(vals.get("company_id"))',
            source,
        )
        self.assertLess(
            source.index("_validate_allowed_company"),
            source.index("super().write"),
        )

    def test_company_validator_uses_allowed_context_and_access_error(self):
        source = self._method_source("_validate_allowed_company")
        self.assertIn('self.env["res.company"].browse(company_id).exists()', source)
        self.assertIn("company not in self.env.companies", source)
        self.assertIn("AccessError", source)
        self.assertNotIn("sudo", source)


if __name__ == "__main__":
    unittest.main()
