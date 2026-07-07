# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError
import re


class TestMrpAutoLotGenerate(TransactionCase):
    
    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.prefix_date_only', 'False'
        )
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.batch_prefix', 'XQ'
        )
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.override_generate_serial', 'True'
        )
        self.product = self.env['product.product'].create({
            'name': 'Test Product',
            'is_storable': True,
            'tracking': 'lot',
        })
        
        self.component = self.env['product.product'].create({
            'name': 'Test Component',
            'is_storable': True,
        })
        
        self.bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'bom_line_ids': [(0, 0, {
                'product_id': self.component.id,
                'product_qty': 1,
            })]
        })
        
        self.production = self.env['mrp.production'].create({
            'product_id': self.product.id,
            'product_qty': 1,
            'bom_id': self.bom.id,
            'origin': 'TEST001',
        })

    def _enable_prefix_date_only(self):
        prefix = f"T{self.product.id}"
        self.product.product_tmpl_id.mrp_lot_prefix = prefix
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.prefix_date_only', 'True'
        )
        return prefix

    def _create_production(self, origin):
        return self.env['mrp.production'].create({
            'product_id': self.product.id,
            'product_qty': 1,
            'bom_id': self.bom.id,
            'origin': origin,
        })

    def _create_lot(self, name):
        return self.env['stock.lot'].create({
            'name': name,
            'product_id': self.product.id,
            'company_id': self.production.company_id.id,
        })

    def _create_finished_move_line(self, production, lot):
        move = production.move_finished_ids.filtered(
            lambda finished_move: finished_move.product_id == production.product_id
        )[:1]
        self.assertTrue(move, "Production must have a finished product move")

        return self.env['stock.move.line'].create({
            'move_id': move.id,
            'production_id': production.id,
            'product_id': production.product_id.id,
            'product_uom_id': production.product_uom_id.id,
            'location_id': move.location_id.id or production.location_src_id.id,
            'location_dest_id': move.location_dest_id.id or production.location_dest_id.id,
            'lot_id': lot.id,
            'quantity': 1,
        })

    def _create_raw_move_line(self, production, lot):
        move = production.move_raw_ids.filtered(
            lambda raw_move: raw_move.product_id == lot.product_id
        )[:1]
        self.assertTrue(move, "Production must have a raw material move for the lot product")

        return self.env['stock.move.line'].create({
            'move_id': move.id,
            'production_id': production.id,
            'product_id': lot.product_id.id,
            'product_uom_id': move.product_uom.id,
            'location_id': move.location_id.id or production.location_src_id.id,
            'location_dest_id': move.location_dest_id.id or production.location_dest_id.id,
            'lot_id': lot.id,
            'quantity': 1,
        })

    def _create_internal_picking_move_line(self, lot):
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', 'in', [self.production.company_id.id, False]),
        ], limit=1)
        self.assertTrue(picking_type, "Internal picking type must exist")

        source_location = self.env.ref('stock.stock_location_stock')
        dest_location = self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            ('id', '!=', source_location.id),
            ('company_id', 'in', [self.production.company_id.id, False]),
        ], limit=1)
        if not dest_location:
            dest_location = self.env['stock.location'].create({
                'name': 'Test Internal Destination',
                'usage': 'internal',
                'location_id': source_location.location_id.id,
                'company_id': self.production.company_id.id,
            })
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
        })
        move = self.env['stock.move'].create({
            'name': lot.product_id.display_name,
            'picking_id': picking.id,
            'picking_type_id': picking_type.id,
            'product_id': lot.product_id.id,
            'product_uom': lot.product_id.uom_id.id,
            'product_uom_qty': 1,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
        })
        return self.env['stock.move.line'].create({
            'picking_id': picking.id,
            'move_id': move.id,
            'product_id': lot.product_id.id,
            'product_uom_id': lot.product_id.uom_id.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'lot_id': lot.id,
            'quantity': 1,
        })

    def test_batch_number_generation(self):
        """测试批次号生成"""
        batch_number = self.production._generate_batch_number()
        
        # 检查批次号格式
        pattern = r'^XQ\d{6}\d{4}A\d{2}$'
        self.assertTrue(re.match(pattern, batch_number), 
                       f"批次号格式不正确: {batch_number}")
        
        # 检查是否包含日期
        from datetime import datetime
        today = datetime.now().strftime('%y%m%d')
        self.assertIn(today, batch_number)

    def test_sub_batch_generation(self):
        """测试已有批次时生成下一个主批次号"""
        # 创建主批次
        self.env['stock.lot'].create({
            'name': 'XQ2410241200A01',
            'product_id': self.product.id,
            'company_id': self.production.company_id.id,
            'ref': 'TEST001',
        })
        
        batch_number = self.production._generate_batch_number()
        
        # 应该继续生成主批次号，不再生成分卷批次号
        self.assertRegex(batch_number, r'^XQ\d{10}A\d{2,3}$')
        self.assertNotIn('-', batch_number)

    def test_batch_prefix_configuration(self):
        """测试批次号前缀配置"""
        # 设置自定义前缀
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.batch_prefix', 'ABC'
        )
        
        batch_number = self.production._generate_batch_number()
        self.assertTrue(batch_number.startswith('ABC'))

    def test_prefix_date_only_returns_prefix_and_date(self):
        """测试只生成前缀和日期开关"""
        prefix = self._enable_prefix_date_only()

        batch_number = self.production._generate_batch_number()

        self.assertRegex(batch_number, rf'^{re.escape(prefix)}\d{{6}}$')

    def test_prefix_date_only_action_creates_lot_without_wizard(self):
        """测试只生成前缀和日期时不弹向导，直接创建批次号"""
        prefix = self._enable_prefix_date_only()

        result = self.production.action_generate_serial()

        self.assertTrue(result)
        self.assertRegex(self.production.lot_producing_id.name, rf'^{re.escape(prefix)}\d{{6}}$')
        self.assertTrue(self.production.lot_producing_id.mrp_auto_lot_needs_suffix)
        self.assertEqual(self.production.lot_producing_id.mrp_auto_lot_production_id, self.production)

    def test_lot_creation_from_production_context_defaults_product(self):
        """测试制造单上下文创建批次时自动带出产品，不需要手动选择"""
        lot_name = f'CTX{self.product.id}A01'

        lot = self.env['stock.lot'].with_context(
            default_production_id=str(self.production.id),
        ).create({
            'name': lot_name,
        })

        self.assertEqual(lot.product_id, self.product)
        self.assertEqual(lot.company_id, self.production.company_id)
        self.assertEqual(lot.mrp_auto_lot_production_id, self.production)

    def test_lot_producing_context_keeps_default_product(self):
        """测试制造单批次字段不再打开批次创建窗口"""
        view = self.env.ref('mrp_auto_lot_generate.view_production_form_auto_lot_generate')

        self.assertIn('mrp_auto_lot_name', view.arch_db)
        self.assertIn("'no_create_edit': True", view.arch_db)
        self.assertIn("'default_product_id': product_id", view.arch_db)
        self.assertIn("'default_production_id': id", view.arch_db)

    def test_prefix_date_only_suffix_completed_on_production_form(self):
        """测试业务人员可在制造单页面补全后缀，不需要打开批次窗口"""
        self._enable_prefix_date_only()
        self.production.action_generate_serial()
        lot = self.production.lot_producing_id
        completed_name = f'{lot.name}A01'

        self.production.write({'mrp_auto_lot_name': completed_name})

        self.assertEqual(lot.name, completed_name)
        self.assertFalse(lot.mrp_auto_lot_needs_suffix)

    def test_prefix_date_only_duplicate_name_rejected(self):
        """测试只生成前缀和日期后，业务人员手工填写仍不能重复"""
        self._enable_prefix_date_only()
        self.production.action_generate_serial()
        completed_name = f"{self.production.lot_producing_id.name}A01"
        self.production.lot_producing_id.name = completed_name

        with self.assertRaises(ValidationError):
            self.env['stock.lot'].create({
                'name': completed_name,
                'product_id': self.product.id,
                'company_id': self.production.company_id.id,
            })

    def test_prefix_date_only_allows_multiple_placeholders(self):
        """测试前缀日期占位批次允许多张制造单各自补全"""
        self._enable_prefix_date_only()
        self.production.action_generate_serial()

        production_2 = self.env['mrp.production'].create({
            'product_id': self.product.id,
            'product_qty': 1,
            'bom_id': self.bom.id,
            'origin': 'TEST002',
        })

        self.assertTrue(production_2.action_generate_serial())
        self.assertTrue(production_2.lot_producing_id)
        self.assertEqual(
            production_2.lot_producing_id.name,
            self.production.lot_producing_id.name,
        )
        self.assertNotEqual(production_2.lot_producing_id, self.production.lot_producing_id)

    def test_prefix_date_only_done_requires_completed_lot_name(self):
        """测试前缀日期占位批次必须补全后才能完成生产"""
        self._enable_prefix_date_only()
        self.production.action_generate_serial()

        with self.assertRaises(ValidationError):
            self.production.button_mark_done()

    def test_duplicate_lot_name_rejected_across_products(self):
        """测试业务人员手填重复批次号时被拦截"""
        duplicate_name = f'DUP{self.product.id}A01'
        self.env['stock.lot'].create({
            'name': duplicate_name,
            'product_id': self.product.id,
            'company_id': self.production.company_id.id,
        })

        other_product = self.env['product.product'].create({
            'name': 'Other Tracked Product',
            'is_storable': True,
            'tracking': 'lot',
        })

        with self.assertRaises(ValidationError):
            self.env['stock.lot'].create({
                'name': duplicate_name,
                'product_id': other_product.id,
                'company_id': self.production.company_id.id,
            })

    def test_lot_producing_id_reuse_rejected(self):
        """测试制造单不能选用已存在批次号"""
        used_lot = self._create_lot(f'USED{self.product.id}A01')

        with self.assertRaises(ValidationError):
            self.production.lot_producing_id = used_lot

    def test_finished_move_line_lot_reuse_rejected(self):
        """测试完成明细不能改成其他制造单已经使用的批次号"""
        self.production.action_generate_serial()
        used_lot = self.production.lot_producing_id
        self._create_finished_move_line(self.production, used_lot)

        production_2 = self._create_production('TEST002')
        production_2.action_generate_serial()
        other_lot = production_2.lot_producing_id
        move_line_2 = self._create_finished_move_line(production_2, other_lot)

        with self.assertRaises(ValidationError):
            move_line_2.lot_id = used_lot

    def test_consuming_produced_lot_as_component_allowed(self):
        """测试已生产批次作为下游原料消耗时不应误判为成品重复使用"""
        self.production.action_generate_serial()
        used_lot = self.production.lot_producing_id
        self._create_finished_move_line(self.production, used_lot)

        downstream_product = self.env['product.product'].create({
            'name': 'Downstream Product',
            'is_storable': True,
            'tracking': 'lot',
        })
        downstream_bom = self.env['mrp.bom'].create({
            'product_tmpl_id': downstream_product.product_tmpl_id.id,
            'bom_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 1,
            })],
        })
        downstream_production = self.env['mrp.production'].create({
            'product_id': downstream_product.id,
            'product_qty': 1,
            'bom_id': downstream_bom.id,
            'origin': 'TEST-DOWNSTREAM',
        })

        self._create_raw_move_line(downstream_production, used_lot)

    def test_same_raw_lot_consumed_by_multiple_productions_allowed(self):
        """测试同一原料批号被多个制造单消耗时不应误判为成品重复使用"""
        raw_lot = self.env['stock.lot'].create({
            'name': f'RAW{self.component.id}A01',
            'product_id': self.component.id,
            'company_id': self.production.company_id.id,
        })
        production_2 = self._create_production('TEST002')

        first_line = self._create_raw_move_line(self.production, raw_lot)
        second_line = self._create_raw_move_line(production_2, raw_lot)

        self.assertEqual(first_line.production_id, self.production)
        self.assertEqual(second_line.production_id, production_2)
        self.assertFalse(first_line.move_id.production_id)
        self.assertFalse(second_line.move_id.production_id)
        self.assertEqual(first_line.move_id.raw_material_production_id, self.production)
        self.assertEqual(second_line.move_id.raw_material_production_id, production_2)

    def test_picking_move_line_with_produced_lot_allowed(self):
        """测试调拨继续使用已生产批次时不应误判为成品重复使用"""
        self.production.action_generate_serial()
        used_lot = self.production.lot_producing_id
        self._create_finished_move_line(self.production, used_lot)

        self._create_internal_picking_move_line(used_lot)

    def test_logging_configuration(self):
        """测试日志配置"""
        # 测试日志启用
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.enable_logging', 'True'
        )
        
        self.assertTrue(self.production._is_logging_enabled())
        
        # 测试日志禁用
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.enable_logging', 'False'
        )
        
        self.assertFalse(self.production._is_logging_enabled())
