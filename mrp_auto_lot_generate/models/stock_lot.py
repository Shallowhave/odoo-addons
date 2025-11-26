# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class StockLot(models.Model):
    _inherit = 'stock.lot'
    
    @api.model_create_multi
    def create(self, vals_list):
        """覆盖 create 方法，当从制造订单创建批次号时，自动生成批次号名称"""
        # 检查是否启用覆盖原生批次号生成
        override_enabled = self.env['ir.config_parameter'].sudo().get_param(
            'mrp_auto_lot_generate.override_generate_serial', 'True'
        ).lower() == 'true'
        
        if not override_enabled:
            # 如果未启用，使用原生行为
            return super(StockLot, self).create(vals_list)
        
        # 检查是否从制造订单上下文创建
        production_id = self.env.context.get('default_production_id') or self.env.context.get('production_id')
        
        if production_id:
            production = self.env['mrp.production'].browse(production_id)
            if production.exists():
                # 检查产品是否需要批次号
                if production.product_id.tracking in ['lot', 'serial']:
                    # 如果 name 为空或未提供，自动生成
                    for vals in vals_list:
                        if not vals.get('name') or not vals.get('name').strip():
                            try:
                                # 生成批次号
                                lot_name = production._generate_batch_number()
                                
                                # 检查批次号是否已存在
                                existing_lot = self.search([
                                    ('name', '=', lot_name),
                                    ('company_id', '=', production.company_id.id)
                                ], limit=1)
                                
                                if existing_lot:
                                    _logger.warning("[自动批次] 批次号 %s 已存在，使用现有批次号", lot_name)
                                    # 如果已存在，使用现有批次号
                                    # 但这里我们不能直接返回，因为这是 create 方法
                                    # 所以继续创建，但使用已存在的名称
                                    vals['name'] = lot_name
                                else:
                                    vals['name'] = lot_name
                                    
                                # 确保产品ID和公司ID正确
                                if 'product_id' not in vals:
                                    vals['product_id'] = production.product_id.id
                                if 'company_id' not in vals:
                                    vals['company_id'] = production.company_id.id
                                    
                                _logger.info("[自动批次] 从制造订单 %s 自动生成批次号：%s", production.name, lot_name)
                            except Exception as e:
                                _logger.error("[自动批次] 从制造订单创建批次号时生成名称失败：%s", str(e))
                                # 如果生成失败，继续使用默认行为（让用户手动输入）
        
        return super(StockLot, self).create(vals_list)

