# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = 'mrp.production'
    
    # RFID 标签关联
    rfid_tag_ids = fields.One2many('rfid.tag', 'production_id', string='RFID 标签')
    rfid_tag_count = fields.Integer(string='RFID 标签数', compute='_compute_rfid_tag_count')
    
    @api.depends('rfid_tag_ids')
    def _compute_rfid_tag_count(self):
        for rec in self:
            rec.rfid_tag_count = len(rec.rfid_tag_ids)
    
    def action_view_rfid_tags(self):
        """查看关联的 RFID 标签"""
        self.ensure_one()
        action = self.env.ref('xq_rfid.rfid_tag_view_act_window').read()[0]
        action['domain'] = [('production_id', '=', self.id)]
        action['context'] = {
            'default_production_id': self.id,
            'default_product_id': self.product_id.id,
            'default_stock_prod_lot_id': self.lot_producing_id.id,
        }
        return action
    
    def generate_rfid_for_lot(self, lot_id=None, quality_check_id=None):
        """
        为批次生成 RFID 标签（公共接口方法）
        
        :param lot_id: 批次/序列号 ID（如果为空则使用 lot_producing_id）
        :param quality_check_id: 质量检查 ID（可选，用于关联质检）
        :return: 创建的 RFID 标签记录
        """
        self.ensure_one()
        
        # 确定批次号
        lot = lot_id or self.lot_producing_id
        if not lot:
            raise UserError(_('无法生成 RFID：未找到批次/序列号！'))
        
        # 检查是否已经存在 RFID 标签
        existing_rfid = self.env['rfid.tag'].search([
            ('stock_prod_lot_id', '=', lot.id)
        ], limit=1)
        
        if existing_rfid:
            return existing_rfid
        
        # 生成新的 RFID 编号
        sequence = self.env.ref('xq_rfid.seq_rfid_tag', raise_if_not_found=False)
        if sequence:
            rfid_number = sequence.next_by_id()
        else:
            rfid_number = f"RFID{self.env['ir.sequence'].next_by_code('rfid.tag') or '001'}"
        
        # 创建 RFID 标签
        rfid_vals = {
            'name': rfid_number,
            'usage_type': 'stock_prod_lot',
            'stock_prod_lot_id': lot.id,
            'product_id': self.product_id.id,
            'production_id': self.id,
            'production_date': self.date_finished or fields.Datetime.now(),
        }
        
        # 如果有质检关联，添加质检信息
        if quality_check_id:
            rfid_vals['quality_check_id'] = quality_check_id
        
        rfid_tag = self.env['rfid.tag'].create(rfid_vals)
        
        return rfid_tag

