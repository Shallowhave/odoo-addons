# -*- coding: utf-8 -*-

from odoo import fields, models, api


class QualityPoint(models.Model):
    _inherit = 'quality.point'
    
    # 测试类型（用于视图中的条件判断）
    test_type = fields.Char(
        string='测试类型',
        related='test_type_id.technical_name',
        readonly=True,
        store=False,
        help='测试类型的技术名称，用于视图条件判断'
    )
    
    # 注意：pad设备自带扫码功能，不需要配置额外设备
    # 扫码功能通过浏览器的条码扫描API或直接输入条码实现
    
    # 扩展 _compute_component_ids 方法，使其也支持 component_scan_verify 类型
    @api.depends('product_ids', 'test_type_id', 'is_workorder_step', 'bom_id')
    def _compute_component_ids(self):
        """
        扩展原生的 _compute_component_ids 方法，使其也支持 component_scan_verify 类型
        """
        # 先调用父类方法
        super()._compute_component_ids()
        
        # 为 component_scan_verify 类型计算组件
        for point in self:
            if point.test_type == 'component_scan_verify' and point.bom_id and not point.component_ids:
                bom_products = point.bom_id.product_id or point.bom_id.product_tmpl_id.product_variant_ids
                # 如果 product_ids 已设置，则只考虑这些产品变体
                if point.product_ids:
                    bom_products &= point.product_ids._origin
                
                component_product_ids = set()
                for product in bom_products:
                    dummy, lines_done = point.bom_id.explode(product, 1.0)
                    component_product_ids |= {line[0].product_id.id for line in lines_done}
                point.component_ids = self.env['product.product'].browse(component_product_ids)

