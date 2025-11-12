# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class MrpProductionReturnHistory(models.Model):
    _name = 'mrp.production.return.history'
    _description = '制造订单剩余产品返回历史'
    _order = 'processed_date desc'
    _rec_name = 'display_name'

    # 基本信息
    production_id = fields.Many2one(
        'mrp.production',
        string='制造订单',
        required=True,
        ondelete='cascade',
        index=True  # 添加索引，常用于查询
    )
    product_id = fields.Many2one(
        'product.product',
        string='产品',
        required=True,
        index=True  # 添加索引，常用于查询
    )
    quantity = fields.Float(
        string='数量',
        required=True,
        digits='Product Unit of Measure'
    )
    
    # 返回策略
    return_strategy = fields.Selection([
        ('before', '返回至生产前'),
        ('after', '返回至生产后'),
        ('defective', '返回至不良品仓'),
        ('scrap', '报废处理'),
    ], string='返回策略', required=True)
    
    target_location_id = fields.Many2one(
        'stock.location',
        string='目标位置',
        help='产品返回的目标位置'
    )
    
    # 原因和备注
    return_reason_id = fields.Many2one(
        'mrp.return.reason',
        string='返回原因',
        help='预设的返回原因'
    )
    custom_reason = fields.Text(
        string='自定义原因',
        help='自定义的返回原因'
    )
    notes = fields.Text(
        string='备注',
        help='额外的处理说明'
    )
    
    # 处理信息
    processed_by = fields.Many2one(
        'res.users',
        string='处理人',
        required=True,
        default=lambda self: self.env.user,
        index=True  # 添加索引，用于按处理人筛选
    )
    processed_date = fields.Datetime(
        string='处理时间',
        required=True,
        default=fields.Datetime.now,
        index=True  # 添加索引，常用于日期范围查询和排序
    )
    
    # 关联记录
    picking_id = fields.Many2one(
        'stock.picking',
        string='调拨单',
        help='创建的库存调拨单'
    )
    move_id = fields.Many2one(
        'stock.move',
        string='调拨明细',
        help='创建的库存调拨明细'
    )
    scrap_id = fields.Many2one(
        'stock.scrap',
        string='报废单',
        help='创建的报废单'
    )
    
    # 状态
    state = fields.Selection([
        ('draft', '草稿'),
        ('done', '完成'),
        ('cancelled', '已取消'),
    ], string='状态', default='draft', required=True, index=True)  # 添加索引，常用于按状态筛选
    
    # 计算字段
    display_name = fields.Char(
        string='显示名称',
        compute='_compute_display_name',
        store=True
    )
    reason_display = fields.Char(
        string='原因显示',
        compute='_compute_reason_display'
    )
    
    @api.depends('production_id', 'product_id', 'quantity', 'processed_date')
    def _compute_display_name(self):
        """计算显示名称"""
        for record in self:
            record.display_name = f"{record.production_id.name} - {record.product_id.name} ({record.quantity}) - {record.processed_date.strftime('%Y-%m-%d %H:%M') if record.processed_date else ''}"
    
    @api.depends('return_reason_id', 'custom_reason')
    def _compute_reason_display(self):
        """计算原因显示"""
        for record in self:
            if record.return_reason_id:
                record.reason_display = record.return_reason_id.name
            elif record.custom_reason:
                record.reason_display = record.custom_reason
            else:
                record.reason_display = '无'

    def action_view_picking(self):
        """查看调拨单"""
        self.ensure_one()
        if not self.picking_id:
            raise UserError('没有关联的调拨单')
        
        return {
            'type': 'ir.actions.act_window',
            'name': '调拨单',
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_scrap(self):
        """查看报废单"""
        self.ensure_one()
        if not self.scrap_id:
            raise UserError('没有关联的报废单')
        
        return {
            'type': 'ir.actions.act_window',
            'name': '报废单',
            'res_model': 'stock.scrap',
            'res_id': self.scrap_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_cancel(self):
        """取消处理"""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('只有草稿状态的记录才能取消')
        
        # 取消关联的调拨单或报废单
        if self.picking_id and self.picking_id.state != 'done':
            self.picking_id.action_cancel()
        if self.scrap_id and self.scrap_id.state != 'done':
            self.scrap_id.action_cancel()
        
        self.state = 'cancelled'
        _logger.info(f"[剩余产品返回] 历史记录 {self.display_name} 已取消")

    def action_done(self):
        """标记为完成"""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('只有草稿状态的记录才能标记为完成')
        
        self.state = 'done'
        _logger.info(f"[剩余产品返回] 历史记录 {self.display_name} 已完成")
