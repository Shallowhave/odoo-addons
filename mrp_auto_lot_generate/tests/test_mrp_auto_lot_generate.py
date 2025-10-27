# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
import re


class TestMrpAutoLotGenerate(TransactionCase):
    
    def setUp(self):
        super().setUp()
        self.product = self.env['product.product'].create({
            'name': 'Test Product',
            'type': 'product',
            'tracking': 'lot',
        })
        
        self.component = self.env['product.product'].create({
            'name': 'Test Component',
            'type': 'product',
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
        """测试分卷批次号生成"""
        # 创建主批次
        main_lot = self.env['stock.lot'].create({
            'name': 'XQ2410241200A01',
            'product_id': self.product.id,
            'ref': 'TEST001',
        })
        
        # 模拟已有主批次的情况
        self.production.origin = 'TEST001'
        batch_number = self.production._generate_batch_number()
        
        # 应该是分卷批次号
        self.assertTrue(batch_number.startswith('XQ2410241200A01-'))
        self.assertIn('-2', batch_number)

    def test_batch_prefix_configuration(self):
        """测试批次号前缀配置"""
        # 设置自定义前缀
        self.env['ir.config_parameter'].sudo().set_param(
            'mrp_auto_lot_generate.batch_prefix', 'ABC'
        )
        
        batch_number = self.production._generate_batch_number()
        self.assertTrue(batch_number.startswith('ABC'))

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
