# -*- coding: utf-8 -*-

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeviceFailClosed(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device = cls.env["rfid.device.config"].create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "company_id": cls.env.company.id,
            "active": True,
        })
        cls.manager = cls.env.ref("base.user_admin")
        cls.manager.groups_id |= cls.env.ref("xq_rfid.group_rfid_manager")
        cls.setup_env = cls.env

    def _create_company(self, name):
        return self.setup_env["res.company"].create({"name": name})

    def _user_with_groups(self, login, *groups, company=None, companies=None):
        company = company or self.env.company
        companies = companies or company
        return self.setup_env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "company_id": company.id,
            "company_ids": [(6, 0, companies.ids)],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                *[group.id for group in groups],
            ])],
        })

    def test_unvalidated_device_is_not_operational(self):
        with self.assertRaisesRegex(UserError, "尚未验证"):
            self.device._ensure_operational()

    def test_inactive_device_is_not_operational(self):
        self.device.active = False
        with self.assertRaisesRegex(UserError, "已停用"):
            self.device._ensure_operational()

    def test_wrong_device_type_is_not_operational(self):
        self.device.device_type = "custom"
        with self.assertRaisesRegex(UserError, "不是 SI120X1"):
            self.device._ensure_operational()

    def test_migration_required_device_is_not_operational(self):
        device = self.env["rfid.device.config"].new({
            "name": "Legacy",
            "device_type": "legacy_disabled",
            "company_id": self.env.company.id,
            "active": True,
        })
        with self.assertRaisesRegex(UserError, "重新配置"):
            device._ensure_operational()

    def test_wrong_company_device_is_not_operational(self):
        other_company = self._create_company("RFID Other")
        device = self.setup_env["rfid.device.config"].with_context(
            allowed_company_ids=(self.setup_env.company | other_company).ids
        ).create({
            "name": "Other company SI120X1",
            "device_type": "si120x1",
            "company_id": other_company.id,
            "active": True,
        })
        device.validation_state = "validated"
        manager = self._user_with_groups(
            "rfid-company-a-manager",
            self.env.ref("xq_rfid.group_rfid_manager"),
            company=self.env.company,
            companies=self.env.company,
        )
        inaccessible_device = device.with_user(manager).with_context(
            allowed_company_ids=[self.env.company.id]
        )
        operations = (
            lambda: inaccessible_device.write_and_verify({"token": "test"}),
            lambda: inaccessible_device.read_memory("3008", "user", 0, 1),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaisesRegex(
                UserError, "无权访问"
            ):
                operation()

    @staticmethod
    def _compatibility_service_calls(service):
        return (
            lambda: service.write_rfid_tag({"token": "test"}),
            service.read_rfid_tag,
            lambda: service.verify_rfid_tag("RFID-TEST"),
            service.erase_rfid_tag,
            service.get_device_status,
        )

    def test_manager_compatibility_service_methods_fail_closed(self):
        service = self.env["rfid.device.service"]
        for call in self._compatibility_service_calls(service):
            with self.subTest(call=call):
                result = call()
                self.assertFalse(result.get("success", result.get("connected")))
                self.assertIn("error", result)

    def test_non_manager_cannot_call_any_compatibility_service_method(self):
        user = self._user_with_groups(
            "rfid-compatibility-non-manager",
            self.env.ref("xq_rfid.group_rfid_user"),
        )
        service = self.env["rfid.device.service"].with_user(user)
        for call in self._compatibility_service_calls(service):
            with self.subTest(call=call), self.assertRaisesRegex(UserError, "管理员"):
                call()

    def test_non_manager_cannot_run_any_device_hardware_action(self):
        user = self._user_with_groups(
            "rfid-non-manager",
            self.env.ref("xq_rfid.group_rfid_user"),
        )
        for action_name in (
            "action_test_connection",
            "action_write_test_tag",
            "action_read_test_tag",
            "action_view_write_logs",
            "action_view_read_logs",
        ):
            with self.subTest(action=action_name), self.assertRaisesRegex(
                UserError, "管理员"
            ):
                getattr(self.device.with_user(user), action_name)()

    def test_non_manager_cannot_call_canonical_hardware_operations(self):
        user = self._user_with_groups(
            "rfid-canonical-non-manager",
            self.env.ref("xq_rfid.group_rfid_user"),
        )
        device = self.device.with_user(user)
        operations = (
            lambda: device.write_and_verify({"token": "test"}),
            lambda: device.read_memory("3008", "user", 0, 1),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaisesRegex(
                UserError, "管理员"
            ):
                operation()

    def test_unvalidated_device_can_reach_connection_probe_boundary(self):
        with self.assertRaisesRegex(UserError, "Adapter 尚未配置"):
            self.device.action_test_connection()

    def _read_wizard(self, **values):
        self.device.validation_state = "validated"
        vals = {
            "device_id": self.device.id,
            "company_id": self.env.company.id,
            "epc_hex": "3008",
        }
        vals.update(values)
        return self.env["rfid.read.wizard"].create(vals)

    def test_valid_read_reaches_canonical_adapter_boundary(self):
        with self.assertRaisesRegex(UserError, "Adapter 尚未配置"):
            self._read_wizard().action_read_rfid()

    def test_canonical_write_propagates_adapter_failure(self):
        self.device.validation_state = "validated"
        with self.assertRaisesRegex(UserError, "Adapter 尚未配置"):
            self.device.write_and_verify({"token": "test"})

    def test_canonical_read_rejects_unvalidated_device(self):
        with self.assertRaisesRegex(UserError, "尚未验证"):
            self.device.read_memory("3008", "user", 0, 1)

    def test_read_wizard_rejects_negative_offset_before_adapter(self):
        with self.assertRaisesRegex(UserError, "不能小于 0"):
            self._read_wizard(word_ptr=-1).action_read_rfid()

    def test_read_wizard_rejects_zero_count_before_adapter(self):
        with self.assertRaisesRegex(UserError, "1 到 128"):
            self._read_wizard(word_count=0).action_read_rfid()

    def test_read_wizard_rejects_count_above_limit_before_adapter(self):
        with self.assertRaisesRegex(UserError, "1 到 128"):
            self._read_wizard(word_count=129).action_read_rfid()

    def test_read_wizard_rejects_invalid_epc_before_adapter(self):
        with self.assertRaisesRegex(UserError, "十六进制"):
            self._read_wizard(epc_hex="XYZ").action_read_rfid()

    def test_read_wizard_rejects_foreign_company_device_on_action(self):
        other_company = self._create_company("Wizard Other")
        companies = self.env.company | other_company
        foreign_device = self.setup_env["rfid.device.config"].with_context(
            allowed_company_ids=companies.ids
        ).create({
            "name": "Wizard foreign device",
            "device_type": "si120x1",
            "company_id": other_company.id,
            "active": True,
        })
        foreign_device.validation_state = "validated"
        wizard = self.env["rfid.read.wizard"].with_context(
            allowed_company_ids=companies.ids
        ).new({
            "company_id": self.env.company.id,
            "device_id": foreign_device.id,
            "epc_hex": "3008",
        })
        with self.assertRaisesRegex(UserError, "当前公司"):
            wizard.action_read_rfid()

    def test_foreign_default_device_is_normalized_to_current_company(self):
        other_company = self._create_company("Default Other")
        companies = self.env.company | other_company
        self.device.validation_state = "validated"
        foreign_device = self.setup_env["rfid.device.config"].with_context(
            allowed_company_ids=companies.ids
        ).create({
            "name": "Foreign default",
            "device_type": "si120x1",
            "company_id": other_company.id,
            "active": True,
        })
        foreign_device.validation_state = "validated"
        defaults = self.env["rfid.read.wizard"].with_context(
            allowed_company_ids=companies.ids,
            default_device_id=foreign_device.id,
        ).default_get(["company_id", "device_id"])
        self.assertEqual(defaults["company_id"], self.env.company.id)
        self.assertEqual(defaults["device_id"], self.device.id)

    def test_ineligible_same_company_defaults_are_normalized(self):
        self.device.validation_state = "validated"
        ineligible_values = (
            {"name": "Inactive default", "device_type": "si120x1", "active": False},
            {"name": "Unvalidated default", "device_type": "si120x1", "active": True},
            {"name": "Wrong type default", "device_type": "custom", "active": True},
        )
        for values in ineligible_values:
            incoming = self.env["rfid.device.config"].create({
                **values,
                "company_id": self.env.company.id,
            })
            defaults = self.env["rfid.read.wizard"].with_context(
                default_device_id=incoming.id,
            ).default_get(["company_id", "device_id"])
            with self.subTest(device=incoming.name):
                self.assertEqual(defaults["company_id"], self.env.company.id)
                self.assertEqual(defaults["device_id"], self.device.id)

    def test_non_manager_cannot_read_from_wizard(self):
        user = self._user_with_groups(
            "rfid-wizard-non-manager",
            self.env.ref("xq_rfid.group_rfid_user"),
        )
        wizard = self._read_wizard().with_user(user)
        with self.assertRaisesRegex(UserError, "管理员"):
            wizard.action_read_rfid()

    def test_read_wizard_has_no_reserve_bank(self):
        selection = dict(
            self.env["rfid.read.wizard"]._fields["mem_bank"]._description_selection(
                self.env
            )
        )
        self.assertNotIn("0x00", selection)

    def test_selectable_domain_is_exact_and_company_aware(self):
        self.assertEqual(
            self.env["rfid.device.config"]._selectable_domain(self.env.company),
            [
                ("device_type", "=", "si120x1"),
                ("active", "=", True),
                ("validation_state", "=", "validated"),
                ("company_id", "=", self.env.company.id),
            ],
        )

    def test_quality_point_create_uses_each_target_company(self):
        other_company = self._create_company("Point Other")
        companies = self.env.company | other_company
        device_model = self.setup_env["rfid.device.config"].with_context(
            allowed_company_ids=companies.ids
        )
        devices = device_model.create([
            {
                "name": "Main selectable",
                "device_type": "si120x1",
                "company_id": self.env.company.id,
                "active": True,
            },
            {
                "name": "Other selectable",
                "device_type": "si120x1",
                "company_id": other_company.id,
                "active": True,
            },
        ])
        devices.validation_state = "validated"
        test_type = self.env.ref("xq_rfid.test_type_rfid_write")
        points = self.env["quality.point"].with_context(
            allowed_company_ids=companies.ids
        ).create([
            {"title": "Main point", "test_type_id": test_type.id, "company_id": self.env.company.id},
            {"title": "Other point", "test_type_id": test_type.id, "company_id": other_company.id},
        ])
        self.assertEqual(points.mapped("rfid_device_id.company_id"), companies)

    def test_quality_point_context_defaults_use_target_company(self):
        other_company = self._create_company("Context Other")
        companies = self.env.company | other_company
        device = self.setup_env["rfid.device.config"].with_context(
            allowed_company_ids=companies.ids
        ).create({
            "name": "Context selectable",
            "device_type": "si120x1",
            "company_id": other_company.id,
            "active": True,
        })
        device.validation_state = "validated"
        test_type = self.env.ref("xq_rfid.test_type_rfid_write")
        point = self.env["quality.point"].with_context(
            allowed_company_ids=companies.ids,
            default_company_id=other_company.id,
            default_test_type_id=test_type.id,
        ).create({"title": "Context point"})
        self.assertEqual(point.company_id, other_company)
        self.assertEqual(point.rfid_device_id, device)

    def _rfid_write_check(self, device):
        team = self.env["quality.alert.team"].search([], limit=1)
        point = self.env["quality.point"].create({
            "title": "RFID write check",
            "test_type_id": self.env.ref("xq_rfid.test_type_rfid_write").id,
            "rfid_device_id": device.id,
        })
        return self.env["quality.check"].create({
            "team_id": team.id,
            "point_id": point.id,
        })

    def test_rfid_write_quality_pass_propagates_adapter_failure(self):
        self.device.validation_state = "validated"
        check = self._rfid_write_check(self.device)
        with self.assertRaisesRegex(UserError, "Adapter 尚未配置"):
            check.do_pass()
        self.assertEqual(check.quality_state, "none")

    def _rfid_label_check(self, production=None, required=True, device=None):
        team = self.env["quality.alert.team"].search([], limit=1)
        point = self.env["quality.point"].create({
            "title": "RFID label check",
            "test_type_id": self.env.ref("xq_rfid.test_type_rfid_label").id,
            "rfid_device_required": required,
            "rfid_device_id": device.id if device else False,
        })
        return self.env["quality.check"].create({
            "team_id": team.id,
            "point_id": point.id,
            "production_id": production.id if production else False,
            "product_id": production.product_id.id if production else False,
        })

    def test_required_rfid_label_without_production_does_not_pass(self):
        check = self._rfid_label_check()
        with self.assertRaisesRegex(UserError, "缺少生产订单"):
            check.do_pass()
        self.assertEqual(check.quality_state, "none")

    def test_required_rfid_label_without_finished_lot_does_not_pass(self):
        product = self.env["product.product"].create({"name": "RFID no lot"})
        production = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1,
            "product_uom_id": product.uom_id.id,
        })
        check = self._rfid_label_check(production=production)
        with self.assertRaisesRegex(UserError, "成品批次"):
            check.do_pass()
        self.assertEqual(check.quality_state, "none")

    def test_required_rfid_label_without_device_does_not_pass(self):
        product = self.env["product.product"].create({
            "name": "RFID product",
            "tracking": "lot",
        })
        production = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1,
            "product_uom_id": product.uom_id.id,
        })
        lot = self.env["stock.lot"].create({
            "name": "RFID-LOT-NO-DEVICE",
            "product_id": product.id,
        })
        production.lot_producing_id = lot
        check = self._rfid_label_check(production=production)
        with self.assertRaisesRegex(UserError, "配置 RFID 设备"):
            check.do_pass()
        self.assertEqual(check.quality_state, "none")

    def test_mixed_recordset_preflights_required_rfid_before_any_pass(self):
        team = self.env["quality.alert.team"].search([], limit=1)
        ordinary = self.env["quality.check"].create({"team_id": team.id})
        required = self._rfid_label_check()
        with self.assertRaises(UserError):
            (ordinary | required).do_pass()
        self.assertEqual(ordinary.quality_state, "none")
        self.assertEqual(required.quality_state, "none")

    def _assert_label_payload_uses_finished_lot(self, existing_tag):
        product = self.env["product.product"].create({
            "name": "RFID label payload product",
            "default_code": "RFID-PAYLOAD-PRODUCT",
            "tracking": "lot",
        })
        production = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1,
            "product_uom_id": product.uom_id.id,
        })
        lot = self.env["stock.lot"].create({
            "name": "RFID-FINISHED-LOT",
            "product_id": product.id,
        })
        production.lot_producing_id = lot
        self.device.validation_state = "validated"
        check = self._rfid_label_check(
            production=production,
            device=self.device,
        )
        if existing_tag:
            check.rfid_tag_id = production.generate_rfid_for_lot(
                lot_id=lot,
                quality_check_id=check.id,
            )
        model_class = type(self.device)
        original = model_class.write_and_verify
        captured = {}

        def capture_and_fail(device, payload):
            captured.update(payload)
            return original(device, payload)

        with patch.object(model_class, "write_and_verify", capture_and_fail):
            with self.assertRaisesRegex(UserError, "Adapter 尚未配置"):
                check.do_pass()
        self.assertEqual(captured["lot_number"], lot.name)
        self.assertEqual(captured["product_code"], product.default_code)
        self.assertEqual(captured["product_name"], product.name)
        self.assertEqual(check.rfid_tag_id.stock_prod_lot_id, lot)
        self.assertEqual(check.quality_state, "none")

    def test_existing_tag_payload_uses_finished_lot_and_adapter_error_propagates(self):
        self._assert_label_payload_uses_finished_lot(existing_tag=True)

    def test_new_tag_payload_uses_finished_lot_and_adapter_error_propagates(self):
        self._assert_label_payload_uses_finished_lot(existing_tag=False)

    def test_existing_tag_from_different_lot_fails_before_adapter_write(self):
        product = self.env["product.product"].create({
            "name": "RFID mismatched tag product",
            "tracking": "lot",
        })
        production = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1,
            "product_uom_id": product.uom_id.id,
        })
        finished_lot, other_lot = self.env["stock.lot"].create([
            {"name": "RFID-FINISHED-EXACT", "product_id": product.id},
            {"name": "RFID-OTHER-LOT", "product_id": product.id},
        ])
        production.lot_producing_id = finished_lot
        check = self._rfid_label_check(production=production, device=self.device)
        check.rfid_tag_id = production.generate_rfid_for_lot(
            lot_id=other_lot,
            quality_check_id=check.id,
        )
        model_class = type(self.device)
        with patch.object(model_class, "write_and_verify") as write_and_verify:
            with self.assertRaisesRegex(UserError, "成品批次"):
                check.do_pass()
        write_and_verify.assert_not_called()
        self.assertEqual(check.quality_state, "none")

    def test_multi_record_non_rfid_checks_can_pass(self):
        team = self.env["quality.alert.team"].search([], limit=1)
        checks = self.env["quality.check"].create([
            {"team_id": team.id},
            {"team_id": team.id},
        ])
        checks.do_pass()
        self.assertEqual(set(checks.mapped("quality_state")), {"pass"})

    def test_optional_rfid_label_without_production_preserves_parent_behavior(self):
        check = self._rfid_label_check(required=False)
        check.do_pass()
        self.assertEqual(check.quality_state, "pass")

    def test_optional_rfid_label_without_finished_lot_preserves_parent_behavior(self):
        product = self.env["product.product"].create({"name": "Optional RFID no lot"})
        production = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1,
            "product_uom_id": product.uom_id.id,
        })
        check = self._rfid_label_check(production=production, required=False)
        check.do_pass()
        self.assertEqual(check.quality_state, "pass")
        self.assertFalse(check.rfid_tag_id)
