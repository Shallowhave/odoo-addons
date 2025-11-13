# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class ByproductLabelWizard(models.TransientModel):
    _name = 'byproduct.label.wizard'
    _description = '副产品标签打印向导'

    production_id = fields.Many2one(
        'mrp.production',
        string='制造订单',
        required=True,
        readonly=True
    )
    
    quality_check_id = fields.Many2one(
        'quality.check',
        string='质检记录',
        readonly=True
    )
    
    byproduct_move_id = fields.Many2one(
        'stock.move',
        string='选择副产品',
        required=True,
        domain="[('id', 'in', available_byproduct_move_ids)]",
        help='选择要打印标签的副产品'
    )
    
    available_byproduct_move_ids = fields.Many2many(
        'stock.move',
        string='可用副产品',
        compute='_compute_available_byproducts',
        help='制造订单中可用的副产品列表'
    )
    
    # 显示字段（只读）
    product_name = fields.Char(
        string='产品名称',
        related='byproduct_move_id.product_id.name',
        readonly=True
    )
    
    product_code = fields.Char(
        string='产品编码',
        related='byproduct_move_id.product_id.default_code',
        readonly=True
    )
    
    quantity = fields.Float(
        string='数量',
        related='byproduct_move_id.product_uom_qty',
        readonly=True
    )
    
    uom_name = fields.Char(
        string='单位',
        related='byproduct_move_id.product_uom.name',
        readonly=True
    )
    
    lot_id = fields.Many2one(
        'stock.lot',
        string='批次号',
        compute='_compute_lot_id',
        readonly=True
    )
    
    @api.depends('production_id')
    def _compute_available_byproducts(self):
        """计算可用的副产品列表"""
        for record in self:
            if record.production_id:
                byproduct_moves = record.production_id.move_byproduct_ids.filtered(
                    lambda m: m.state in ('done', 'assigned') and m.product_uom_qty > 0
                )
                record.available_byproduct_move_ids = byproduct_moves
            else:
                record.available_byproduct_move_ids = False
    
    @api.depends('byproduct_move_id')
    def _compute_lot_id(self):
        """计算批次号"""
        for record in self:
            if record.byproduct_move_id and record.byproduct_move_id.move_line_ids:
                # 获取第一个移动行的批次号
                lot = record.byproduct_move_id.move_line_ids[0].lot_id
                record.lot_id = lot if lot else False
            else:
                record.lot_id = False
    
    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super().default_get(fields_list)
        
        # 从 context 中获取制造订单和质检记录
        production_id = self.env.context.get('default_production_id')
        quality_check_id = self.env.context.get('default_quality_check_id')
        
        if production_id:
            res['production_id'] = production_id
        if quality_check_id:
            res['quality_check_id'] = quality_check_id
        
        return res
    
    def action_print_label(self):
        """打印选中的副产品标签"""
        self.ensure_one()
        
        if not self.byproduct_move_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('错误'),
                    'message': _('请选择要打印的副产品！'),
                    'type': 'danger',
                    'sticky': False,
                }
            }
        
        production = self.production_id
        byproduct_move = self.byproduct_move_id
        
        # 准备 context
        ctx = dict(self.env.context or {})
        paper = self.env.ref('xq_mrp_label.paperformat_100x100', raise_if_not_found=False)
        if paper:
            ctx['force_paperformat_id'] = paper.id
        
        # 将副产品移动记录添加到 context 中
        ctx['byproduct_move'] = byproduct_move
        
        # 打印标签
        xmlid = 'xq_mrp_label.action_report_mrp_byproduct_label'
        action = self.env.ref(xmlid)
        return action.report_action(production, context=ctx)

