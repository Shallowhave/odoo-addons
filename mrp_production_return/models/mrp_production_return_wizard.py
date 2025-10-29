# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging
from datetime import datetime

_logger = logging.getLogger(__name__)


class MrpProductionReturnWizard(models.TransientModel):
    _name = 'mrp.production.return.wizard'
    _description = '制造订单剩余组件返回向导'

    production_id = fields.Many2one(
        'mrp.production',
        string='制造订单',
        required=True,
        readonly=True
    )
    component_line_ids = fields.One2many(
        'mrp.production.return.wizard.line',
        'wizard_id',
        string='剩余组件',
        help='需要处理的剩余组件列表'
    )
    return_strategy = fields.Selection([
        ('defective', '返回至不良品仓'),
        ('main', '返回至主仓库'),
        ('custom', '返回至自定义位置'),
        ('scrap', '报废处理'),
    ], string='返回策略', required=True, default='defective')
    
    # 位置选择
    defective_location_id = fields.Many2one(
        'stock.location',
        string='不良品仓',
        domain="[('usage', '=', 'internal'), ('scrap_location', '=', True)]",
        help='选择不良品仓位置'
    )
    main_location_id = fields.Many2one(
        'stock.location',
        string='主仓库',
        domain="[('usage', '=', 'internal'), ('scrap_location', '=', False)]",
        help='选择主仓库位置'
    )
    custom_location_id = fields.Many2one(
        'stock.location',
        string='自定义位置',
        domain="[('usage', '=', 'internal')]",
        help='选择自定义返回位置'
    )
    
    # 原因和备注
    return_reason_id = fields.Many2one(
        'mrp.return.reason',
        string='返回原因',
        help='选择预设的返回原因'
    )
    custom_reason = fields.Text(
        string='自定义原因',
        help='如果选择其他，请填写具体原因'
    )
    notes = fields.Text(
        string='备注',
        help='额外的处理说明'
    )
    
    # 处理选项
    auto_confirm_picking = fields.Boolean(
        string='自动确认调拨单',
        default=True,
        help='是否自动确认创建的调拨单'
    )
    send_notification = fields.Boolean(
        string='发送通知',
        default=True,
        help='是否发送处理完成通知'
    )
    
    # 计算字段
    target_location_id = fields.Many2one(
        'stock.location',
        string='目标位置',
        compute='_compute_target_location',
        store=True
    )
    location_name = fields.Char(
        string='位置名称',
        compute='_compute_location_name'
    )

    @api.depends('return_strategy', 'defective_location_id', 'main_location_id', 'custom_location_id')
    def _compute_target_location(self):
        """计算目标位置"""
        for record in self:
            if record.return_strategy == 'defective':
                record.target_location_id = record.defective_location_id
            elif record.return_strategy == 'main':
                record.target_location_id = record.main_location_id
            elif record.return_strategy == 'custom':
                record.target_location_id = record.custom_location_id
            else:
                record.target_location_id = False

    @api.depends('target_location_id')
    def _compute_location_name(self):
        """计算位置名称"""
        for record in self:
            record.location_name = record.target_location_id.name if record.target_location_id else ''

    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super().default_get(fields_list)
        
        # 从上下文获取默认值
        if 'default_production_id' in self.env.context:
            production = self.env['mrp.production'].browse(self.env.context['default_production_id'])
            res['production_id'] = production.id
            
            # 获取剩余组件
            remaining_moves = production.move_raw_ids.filtered(
                lambda m: m.state in ('done', 'assigned', 'partially_available') and m.product_uom_qty > m.quantity
            )
            
            # 创建组件行
            component_lines = []
            for move in remaining_moves:
                component_lines.append((0, 0, {
                    'move_id': move.id,
                    'return_qty': move.product_uom_qty - move.quantity,
                }))
            res['component_line_ids'] = component_lines
            
            # 智能推荐位置
            # 获取公司的默认仓库
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', production.company_id.id)
            ], limit=1)
            
            if warehouse:
                # 推荐不良品仓
                defective_loc = self.env['stock.location'].search([
                    ('usage', '=', 'internal'),
                    ('scrap_location', '=', True),
                    ('warehouse_id', '=', warehouse.id)
                ], limit=1)
                if defective_loc:
                    res['defective_location_id'] = defective_loc.id
                
                # 推荐主仓库
                main_loc = warehouse.lot_stock_id
                if main_loc:
                    res['main_location_id'] = main_loc.id
                
        return res

    @api.onchange('return_strategy')
    def _onchange_return_strategy(self):
        """返回策略变更时的处理"""
        if self.return_strategy == 'scrap':
            self.auto_confirm_picking = False

    def _validate_data(self):
        """验证数据"""
        if not self.remaining_qty or self.remaining_qty <= 0:
            raise ValidationError('剩余数量必须大于0')
        
        if not self.target_location_id:
            strategy_names = {
                'defective': '不良品仓',
                'main': '主仓库', 
                'custom': '自定义位置'
            }
            raise ValidationError(f'请选择{strategy_names.get(self.return_strategy, "目标")}位置')
        
        if self.return_strategy == 'scrap' and not self.return_reason_id:
            raise ValidationError('报废处理必须选择返回原因')

    def action_confirm_return(self):
        """确认返回剩余组件 - 优化版本"""
        self.ensure_one()
        
        # 验证数据
        self._validate_data()
        
        try:
            # 处理每个组件行
            for line in self.component_line_ids:
                if line.return_qty > 0:
                    # 创建返回历史记录
                    history_vals = {
                        'production_id': self.production_id.id,
                        'product_id': line.product_id.id,
                        'quantity': line.return_qty,
                        'return_strategy': self.return_strategy,
                        'target_location_id': self.target_location_id.id,
                        'return_reason_id': self.return_reason_id.id if self.return_reason_id else False,
                        'custom_reason': self.custom_reason,
                        'notes': self.notes,
                        'processed_by': self.env.user.id,
                        'processed_date': fields.Datetime.now(),
                    }
                    history = self.env['mrp.production.return.history'].create(history_vals)
                    
                    # 根据策略处理
                    if self.return_strategy == 'scrap':
                        self._process_scrap_return(history, line)
                    else:
                        self._process_location_return(history, line)
            
            # 发送通知
            if self.send_notification:
                self._send_notification()
            
            # 记录日志
            _logger.info(f"[剩余组件返回] 制造订单 {self.production_id.name} 的剩余组件已处理完成")
            
            # 完成制造订单
            self.production_id.with_context(skip_backorder=True).button_mark_done()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '处理完成',
                    'message': f'剩余组件已成功处理，制造订单已完成',
                    'type': 'success',
                }
            }
            
        except Exception as e:
            _logger.error(f"[剩余组件返回] 处理失败: {str(e)}")
            raise UserError(f'处理失败: {str(e)}')

    def _process_location_return(self, history, line):
        """处理位置返回"""
        # 获取源位置
        source_location = self.production_id.location_src_id
        if not source_location:
            raise UserError('无法找到制造订单的源位置')
        
        # 获取公司的默认仓库
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.production_id.company_id.id)
        ], limit=1)
        
        if not warehouse:
            raise UserError('无法找到公司的仓库')
        
        # 创建调拨单类型
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('warehouse_id', '=', warehouse.id)
        ], limit=1)
        
        if not picking_type:
            raise UserError('无法找到内部调拨单类型')
        
        # 创建库存调拨单
        picking_vals = {
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': self.target_location_id.id,
            'origin': f'制造订单剩余组件返回 - {self.production_id.name}',
            'note': f'剩余组件返回处理\n策略: {dict(self._fields["return_strategy"].selection)[self.return_strategy]}\n原因: {self.return_reason_id.name if self.return_reason_id else self.custom_reason or "无"}',
            'user_id': self.env.user.id,
        }
        
        picking = self.env['stock.picking'].create(picking_vals)
        
        # 创建调拨明细
        move_vals = {
            'name': f'剩余组件返回 - {line.product_id.name}',
            'product_id': line.product_id.id,
            'product_uom_qty': line.return_qty,
            'product_uom': line.product_id.uom_id.id,
            'location_id': source_location.id,
            'location_dest_id': self.target_location_id.id,
            'picking_id': picking.id,
            'origin': f'制造订单剩余组件返回 - {self.production_id.name}',
        }
        
        move = self.env['stock.move'].create(move_vals)
        
        # 更新历史记录
        history.write({
            'picking_id': picking.id,
            'move_id': move.id,
        })
        
        # 自动确认调拨单
        if self.auto_confirm_picking:
            picking.action_confirm()
            if picking.state == 'assigned':
                picking.button_validate()

    def _process_scrap_return(self, history, line):
        """处理报废返回"""
        # 创建报废单
        scrap_vals = {
            'product_id': line.product_id.id,
            'scrap_qty': line.return_qty,
            'product_uom_id': line.product_id.uom_id.id,
            'location_id': self.production_id.location_src_id.id,
            'origin': f'制造订单剩余组件报废 - {self.production_id.name}',
            'note': f'剩余组件报废处理\n原因: {self.return_reason_id.name if self.return_reason_id else self.custom_reason or "无"}',
        }
        
        scrap = self.env['stock.scrap'].create(scrap_vals)
        scrap.action_validate()
        
        # 更新历史记录
        history.write({
            'scrap_id': scrap.id,
        })

    def _send_notification(self):
        """发送通知"""
        # 这里可以实现邮件或系统通知
        pass