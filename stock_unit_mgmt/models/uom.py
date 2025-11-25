# -*- coding: utf-8 -*-

from odoo import models, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class UomUom(models.Model):
    _inherit = 'uom.uom'
    
    @api.model_create_multi
    def create(self, vals_list):
        """创建 UOM 时，确保 rounding 不为 0"""
        default_rounding = 0.01
        
        for vals in vals_list:
            # 如果 rounding 未设置或为 0，设置默认值
            if 'rounding' not in vals or not vals.get('rounding') or vals.get('rounding') <= 0:
                vals['rounding'] = default_rounding
                _logger.warning(
                    "[UOM修复] 创建 UOM 时 rounding 为 0，已设置为默认值 %s: %s",
                    default_rounding, vals.get('name', 'Unknown')
                )
        
        return super(UomUom, self).create(vals_list)
    
    def write(self, vals):
        """更新 UOM 时，确保 rounding 不为 0"""
        default_rounding = 0.01
        
        # 如果更新 rounding 字段，确保不为 0
        if 'rounding' in vals:
            if not vals['rounding'] or vals['rounding'] <= 0:
                vals['rounding'] = default_rounding
                _logger.warning(
                    "[UOM修复] 更新 UOM 时 rounding 为 0，已设置为默认值 %s: UOM IDs=%s",
                    default_rounding, self.ids
                )
        
        result = super(UomUom, self).write(vals)
        
        # 写入后检查：如果 rounding 仍然为 0，修复它
        for record in self:
            if not record.rounding or record.rounding <= 0:
                record.sudo().write({'rounding': default_rounding})
                record.invalidate_recordset(['rounding'])
                _logger.warning(
                    "[UOM修复] 写入后检查发现 rounding 为 0，已修复: UOM=%s (ID=%s)",
                    record.name, record.id
                )
        
        return result
    
    @api.model
    def _fix_zero_rounding(self):
        """修复所有 rounding 为 0 的 UOM（可以通过计划任务调用）"""
        default_rounding = 0.01
        uoms_to_fix = self.search([('rounding', '<=', 0)])
        
        if uoms_to_fix:
            uoms_to_fix.sudo().write({'rounding': default_rounding})
            _logger.info(
                "[UOM修复] 批量修复了 %d 个 UOM 的 rounding 值",
                len(uoms_to_fix)
            )
        
        return len(uoms_to_fix)

