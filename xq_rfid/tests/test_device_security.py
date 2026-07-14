from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRfidDeviceSecurity(TransactionCase):
    @classmethod
    def _ensure_mrp_picking_type(cls, company):
        warehouse = cls.setup_env["stock.warehouse"].search([
            ("company_id", "=", company.id),
        ], limit=1)
        if not warehouse:
            warehouse = cls.setup_env["stock.warehouse"].create({
                "name": f"{company.name} Security Warehouse",
                "code": f"S{company.id}",
                "company_id": company.id,
            })
        picking_type = warehouse.manu_type_id or cls.setup_env[
            "stock.picking.type"
        ].search([
            ("code", "=", "mrp_operation"),
            ("company_id", "=", company.id),
        ], limit=1)
        assert picking_type, f"Missing manufacturing picking type for {company.name}"
        return picking_type

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_env = cls.env
        cls.company_a = cls.setup_env.company
        cls.company_b = cls.setup_env["res.company"].create({
            "name": "RFID Security Other",
        })
        cls.companies = cls.company_a | cls.company_b
        device_model = cls.setup_env["rfid.device.config"].sudo().with_context(
            allowed_company_ids=cls.companies.ids,
        )
        cls.device_a, cls.device_b = device_model.create([
            {
                "name": "RFID company A device",
                "device_type": "si120x1",
                "company_id": cls.company_a.id,
            },
            {
                "name": "RFID company B device",
                "device_type": "si120x1",
                "company_id": cls.company_b.id,
            },
        ])
        cls.quality_team = cls.setup_env["quality.alert.team"].create({
            "name": "RFID Security Quality Team",
            "company_id": cls.company_a.id,
        })
        cls.mrp_picking_type = cls._ensure_mrp_picking_type(cls.company_a)
        cls.quality_point = cls.setup_env["quality.point"].create({
            "title": "RFID Security Quality Point",
            "team_id": cls.quality_team.id,
            "test_type_id": cls.setup_env.ref("xq_rfid.test_type_rfid_write").id,
            "company_id": cls.company_a.id,
            "rfid_device_id": cls.device_a.id,
            "picking_type_ids": [(6, 0, cls.mrp_picking_type.ids)],
        })
        cls.rfid_user = cls._create_user(
            "rfid-security-user",
            cls.setup_env.ref("xq_rfid.group_rfid_user"),
        )
        cls.rfid_manager = cls._create_user(
            "rfid-security-manager",
            cls.setup_env.ref("xq_rfid.group_rfid_manager"),
        )

    @classmethod
    def _create_user(cls, login, rfid_group):
        return cls.setup_env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": login,
            "login": login,
            "company_id": cls.company_a.id,
            "company_ids": [(6, 0, cls.companies.ids)],
            "groups_id": [(6, 0, [
                cls.setup_env.ref("base.group_user").id,
                cls.setup_env.ref("base.group_multi_company").id,
                rfid_group.id,
            ])],
        })

    def _device_model(self, user, companies):
        return self.setup_env["rfid.device.config"].with_user(user).with_context(
            allowed_company_ids=companies.ids,
        )

    def test_user_and_manager_search_only_allowed_company_devices(self):
        device_ids = (self.device_a | self.device_b).ids
        for user in (self.rfid_user, self.rfid_manager):
            with self.subTest(user=user.login, allowed="company_a"):
                visible = self._device_model(user, self.company_a).search([
                    ("id", "in", device_ids),
                ])
                self.assertEqual(visible.ids, self.device_a.ids)
            with self.subTest(user=user.login, allowed="both"):
                visible = self._device_model(user, self.companies).search([
                    ("id", "in", device_ids),
                ])
                self.assertEqual(set(visible.ids), set(device_ids))

    def test_manager_can_create_and_write_in_an_allowed_company(self):
        device = self._device_model(self.rfid_manager, self.company_b).create({
            "name": "Allowed company B manager device",
            "device_type": "si120x1",
            "company_id": self.company_b.id,
        })

        device.write({"name": "Updated company B manager device"})

        self.assertEqual(device.name, "Updated company B manager device")
        self.assertEqual(device.company_id, self.company_b)

    def test_manager_cannot_create_for_a_disallowed_company(self):
        with self.assertRaises(AccessError):
            self._device_model(self.rfid_manager, self.company_a).create({
                "name": "Disallowed company B manager device",
                "device_type": "si120x1",
                "company_id": self.company_b.id,
            })

    def test_manager_cannot_mutate_a_disallowed_company_device(self):
        device = self.device_b.with_user(self.rfid_manager).with_context(
            allowed_company_ids=self.company_a.ids,
        )

        with self.assertRaises(AccessError):
            device.write({"name": "Forbidden update"})

    def test_manager_cannot_move_device_outside_allowed_companies(self):
        device = self.device_a.with_user(self.rfid_manager).with_context(
            allowed_company_ids=self.company_a.ids,
        )

        with self.assertRaises(AccessError):
            device.write({"company_id": self.company_b.id})

    def test_quality_point_rejects_cross_company_device_on_write(self):
        with self.assertRaisesRegex(ValidationError, "same company"):
            self.quality_point.write({"rfid_device_id": self.device_b.id})

    def test_user_acl_still_blocks_mutation_of_visible_device(self):
        device_model = self._device_model(self.rfid_user, self.company_a)
        device = self.device_a.with_user(self.rfid_user).with_context(
            allowed_company_ids=self.company_a.ids,
        )

        with self.assertRaises(AccessError):
            device.write({"name": "User update"})
        with self.assertRaises(AccessError):
            device_model.create({
                "name": "User-created device",
                "device_type": "si120x1",
                "company_id": self.company_a.id,
            })
        with self.assertRaises(AccessError):
            device.unlink()

    def test_manager_acl_still_blocks_unlink_of_visible_device(self):
        device = self.device_a.with_user(self.rfid_manager).with_context(
            allowed_company_ids=self.company_a.ids,
        )

        with self.assertRaises(AccessError):
            device.unlink()

    def test_superuser_setup_can_access_cross_company_fixtures(self):
        visible = self.setup_env["rfid.device.config"].sudo().with_context(
            allowed_company_ids=self.company_a.ids,
        ).search([
            ("id", "in", (self.device_a | self.device_b).ids),
        ])

        self.assertEqual(set(visible.ids), set((self.device_a | self.device_b).ids))
