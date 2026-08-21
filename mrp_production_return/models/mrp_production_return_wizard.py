# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero
import logging

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
    company_id = fields.Many2one(
        'res.company',
        string='公司',
        related='production_id.company_id',
        readonly=True,
        store=False,
    )
    complete_production_after_return = fields.Boolean(
        string='处理后完成制造订单',
        help='从不分卷/无欠单流程打开时，剩余组件处理完成后继续执行制造订单完成流程。'
    )
    component_line_ids = fields.One2many(
        'mrp.production.return.wizard.line',
        'wizard_id',
        string='剩余组件',
        help='需要处理的剩余组件列表'
    )
    return_strategy = fields.Selection([
        ('before', '返回至生产前'),
        ('after', '返回至生产后'),
        ('defective', '返回至不良品仓'),
        ('scrap', '报废处理'),
    ], string='返回策略', required=True, default='before')
    
    # 位置选择
    defective_location_id = fields.Many2one(
        'stock.location',
        string='不良品仓',
        domain="[('usage', '=', 'internal'), ('scrap_location', '=', False), '|', ('company_id', '=', company_id), ('company_id', '=', False)]",
        help='选择不良品仓位置（用于存放不合格但仍存在的产品）'
    )
    scrap_location_id = fields.Many2one(
        'stock.location',
        string='报废仓库',
        domain="[('scrap_location', '=', True), '|', ('company_id', '=', company_id), ('company_id', '=', False)]",
        help='选择报废仓库位置（用于存放报废物料）'
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

    @api.depends('return_strategy', 'production_id', 'defective_location_id', 'scrap_location_id')
    def _compute_target_location(self):
        """计算目标位置"""
        for record in self:
            if record.return_strategy == 'before':
                # 返回至生产前：使用制造订单的源位置（原材料位置）
                record.target_location_id = record.production_id.location_src_id if record.production_id else False
            elif record.return_strategy == 'after':
                # 返回至生产后：使用制造订单的目标位置（成品位置）
                record.target_location_id = record.production_id.location_dest_id if record.production_id else False
            elif record.return_strategy == 'defective':
                record.target_location_id = record.defective_location_id
            elif record.return_strategy == 'scrap':
                record.target_location_id = record.scrap_location_id
            else:
                record.target_location_id = False

    @api.depends('target_location_id')
    def _compute_location_name(self):
        """计算位置名称"""
        for record in self:
            record.location_name = record.target_location_id.name if record.target_location_id else ''

    def _recommend_defective_location(self, warehouse):
        """推荐不良品仓库位置"""
        # 优先查找名称包含"不良"或"次品"的内部库位
        defective_loc = self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            ('scrap_location', '=', False),
            ('warehouse_id', '=', warehouse.id),
            '|', ('name', 'ilike', '不良'),
            ('name', 'ilike', '次品')
        ], limit=1)
        
        # 如果没有专门的不良品仓，使用主仓库的子位置
        if not defective_loc:
            defective_loc = self.env['stock.location'].search([
                ('usage', '=', 'internal'),
                ('scrap_location', '=', False),
                ('warehouse_id', '=', warehouse.id),
                ('location_id', '!=', False)  # 有父位置的子库位
            ], limit=1)
        
        return defective_loc
    
    
    def _recommend_scrap_location(self, company):
        """推荐报废仓库位置"""
        return self.env['stock.location'].search([
            ('scrap_location', '=', True),
            '|', ('company_id', '=', company.id),
            ('company_id', '=', False)
        ], limit=1)

    @api.model
    def _get_unprocessed_remaining_moves(self, production):
        """Return remaining raw moves that have not been processed by this return flow."""
        if not production:
            return self.env['stock.move']

        return production._get_unprocessed_remaining_components()

    def _get_existing_component_move_ids(self):
        """Return source move IDs already present in this wizard."""
        return self.component_line_ids.filtered('move_id').mapped('move_id').ids

    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super().default_get(fields_list)
        
        # 从上下文获取默认值
        if 'default_production_id' in self.env.context:
            production = self.env['mrp.production'].browse(self.env.context['default_production_id'])
            res['production_id'] = production.id
            
            if 'default_complete_production_after_return' in self.env.context:
                res['complete_production_after_return'] = bool(
                    self.env.context.get('default_complete_production_after_return')
                )

            # 获取按源移动过滤后的剩余组件，避免同一产品的多批次/多移动互相隐藏。
            remaining_moves = self._get_unprocessed_remaining_moves(production)
            
            # 自动填充剩余组件行
            _logger.info(f"[剩余组件向导] 开始填充组件行，找到 {len(remaining_moves)} 个剩余组件")
            component_lines = []
            for move in remaining_moves:
                remaining_qty = move.product_uom_qty - move.quantity
                _logger.info(
                    f"[剩余组件向导] 处理组件: 产品={move.product_id.name}(ID:{move.product_id.id}), "
                    f"移动ID={move.id}, 计划数量={move.product_uom_qty}, 已消耗={move.quantity}, "
                    f"剩余数量={remaining_qty}, 单位={move.product_uom.name if move.product_uom else 'None'}"
                )
                
                # 关键修复：必须设置 move_id，否则 related 字段（product_uom_id, expected_qty等）无法获取值
                # 同时需要确保 wizard_id 在 context 中，以便计算字段能够正确计算
                component_lines.append((0, 0, {
                    'move_id': move.id,  # 添加 move_id
                    # 注意：product_id 现在是从 move_id 自动关联的，不需要手动设置
                    # 但是在 wizard_line 中，product_id 不再是 related 字段，所以需要手动设置
                    'product_id': move.product_id.id,
                    'return_qty': remaining_qty,
                }))
                _logger.info(
                    f"[剩余组件向导] 已创建组件行: move_id={move.id}, product_id={move.product_id.id}, "
                    f"return_qty={remaining_qty}"
                )
            
            res['component_line_ids'] = component_lines
            _logger.info(f"[剩余组件向导] 共创建 {len(component_lines)} 个组件行")
            
            # 关键修复：在创建向导后，需要确保组件行的 available_product_ids 被正确计算
            # 这会在创建向导对象时自动触发，因为 @api.depends 会监听 wizard_id 的变化
            
            # 智能推荐位置
            warehouse = production.picking_type_id.warehouse_id or self.env['stock.warehouse'].search([
                ('company_id', '=', production.company_id.id)
            ], limit=1)

            if warehouse:
                # 使用提取的方法推荐位置
                defective_loc = self._recommend_defective_location(warehouse)
                if defective_loc:
                    res['defective_location_id'] = defective_loc.id
                
                scrap_loc = self._recommend_scrap_location(production.company_id)
                if scrap_loc:
                    res['scrap_location_id'] = scrap_loc.id
                
        return res

    @api.onchange('return_strategy')
    def _onchange_return_strategy(self):
        """返回策略变更时的处理"""
        # 报废处理也自动确认调拨单（现在是转移到报废仓库，不再是永久删除）
        pass

    def _validate_data(self):
        """验证数据"""
        self.ensure_one()
        if not self.production_id:
            raise ValidationError('请选择制造订单')
        if not self.component_line_ids:
            raise ValidationError('没有需要处理的剩余组件')

        valid_lines = self.component_line_ids.filtered(lambda line: line.return_qty > 0)
        if not valid_lines:
            raise ValidationError('至少需要一个组件的返回数量大于0')

        if not self.target_location_id:
            strategy_names = {
                'before': '生产前',
                'after': '生产后',
                'defective': '不良品仓',
                'scrap': '报废仓库'
            }
            if self.return_strategy in ('before', 'after'):
                raise ValidationError(f'无法找到{strategy_names.get(self.return_strategy, "目标")}位置，请检查制造订单的位置设置')
            raise ValidationError(f'请选择{strategy_names.get(self.return_strategy, "目标")}位置')

        if self.target_location_id.company_id and self.target_location_id.company_id != self.production_id.company_id:
            raise ValidationError('目标位置必须属于制造订单公司或共享位置')

        if self.return_strategy == 'scrap' and not self.return_reason_id:
            raise ValidationError('报废处理必须选择返回原因')
        if self.return_reason_id and self.return_reason_id.company_id and self.return_reason_id.company_id != self.production_id.company_id:
            raise ValidationError('返回原因必须属于制造订单公司或共享原因')

        available_moves = self._get_unprocessed_remaining_moves(self.production_id)
        seen_move_ids = set()
        for line in valid_lines:
            if not line.move_id:
                raise ValidationError('组件行缺少源库存移动，无法安全处理')
            if line.move_id.id in seen_move_ids:
                raise ValidationError(f'组件 {line.product_id.display_name} 的源库存移动重复，请删除重复行')
            seen_move_ids.add(line.move_id.id)
            if line.move_id not in self.production_id.move_raw_ids:
                raise ValidationError(f'组件 {line.product_id.display_name} 的库存移动不属于当前制造订单')
            if line.move_id not in available_moves:
                raise ValidationError(f'组件 {line.product_id.display_name} 已处理或不再有剩余数量')
            if line.product_id != line.move_id.product_id:
                raise ValidationError('组件行产品必须与源库存移动产品一致')
            if line.move_id.company_id and line.move_id.company_id != self.production_id.company_id:
                raise ValidationError(f'组件 {line.product_id.display_name} 的库存移动公司与制造订单公司不一致')
            precision = self._get_quantity_precision(line)
            current_remaining = line.move_id.product_uom_qty - line.move_id.quantity
            if float_compare(line.return_qty, current_remaining, precision_rounding=precision) > 0:
                raise ValidationError(
                    f'组件 {line.product_id.display_name} 的返回数量不能超过实时剩余数量！\n'
                    f'实时剩余数量：{current_remaining} {line.product_uom_id.name}\n'
                    f'您输入的返回数量：{line.return_qty} {line.product_uom_id.name}'
                )
        return valid_lines

    def _lock_source_moves(self, lines):
        """Serialize returns for the same source moves."""
        move_ids = sorted(lines.mapped('move_id').ids)
        self.env.cr.execute(
            'SELECT id FROM stock_move '
            'WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(move_ids)],
        )
        if [row[0] for row in self.env.cr.fetchall()] != move_ids:
            raise ValidationError('一个或多个源库存移动已不存在，请关闭向导后重试')
        # Odoo uses REPEATABLE READ. A no-op update makes a stale concurrent
        # transaction raise SerializationFailure and retry with a fresh snapshot.
        self.env.cr.execute(
            'UPDATE stock_move SET write_date = write_date WHERE id IN %s',
            [tuple(move_ids)],
        )

    def _get_quantity_field_name(self):
        """Return the done quantity field for the installed Odoo version."""
        move_line_fields = self.env['stock.move.line']._fields
        return 'qty_done' if 'qty_done' in move_line_fields else 'quantity'

    def _get_source_move_line_quantity(self, move_line):
        """Return quantity available on a source move line for lot split allocation."""
        for field_name in ('quantity', 'qty_done'):
            if field_name in move_line._fields:
                quantity = move_line[field_name] or 0.0
                if quantity > 0:
                    return quantity
        return 0.0

    def _get_quantity_precision(self, line):
        return (
            (line.product_uom_id and line.product_uom_id.rounding)
            or (line.product_id.uom_id and line.product_id.uom_id.rounding)
            or 0.0001
        )

    def _prepare_return_move_line_vals(self, move, line, source_location, dest_location, quantity, source_line=False):
        move_line_fields = self.env['stock.move.line']._fields
        quantity_field = self._get_quantity_field_name()
        vals = {
            'move_id': move.id,
            'product_id': line.product_id.id,
            'product_uom_id': move.product_uom.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            quantity_field: quantity,
        }
        if source_line:
            if source_line.lot_id:
                vals['lot_id'] = source_line.lot_id.id
            elif 'lot_name' in move_line_fields and source_line.lot_name:
                vals['lot_name'] = source_line.lot_name
        return vals

    def _create_return_move_lines_from_source(self, move, line, source_location, dest_location):
        """Create return move lines by preserving the source component lots."""
        precision = self._get_quantity_precision(line)
        quantity_to_return = line.return_qty
        if float_is_zero(quantity_to_return, precision_rounding=precision):
            return self.env['stock.move.line']

        source_move_lines = line.move_id.move_line_ids.filtered(
            lambda ml: ml.lot_id or getattr(ml, 'lot_name', False)
        ).sorted('id') if line.move_id else self.env['stock.move.line']
        positive_source_lines = source_move_lines.filtered(
            lambda ml: self._get_source_move_line_quantity(ml) > 0
        )

        created_lines = self.env['stock.move.line']
        remaining_qty = quantity_to_return

        if source_move_lines and not positive_source_lines:
            if len(source_move_lines) == 1:
                vals = self._prepare_return_move_line_vals(
                    move, line, source_location, dest_location, remaining_qty, source_move_lines[0]
                )
                return self.env['stock.move.line'].create(vals)
            raise UserError(
                '源组件移动存在多个批次，但批次数量不可用，无法安全拆分退库。\n'
                '请检查制造订单组件移动行的批次数量后再处理。'
            )

        for source_line in positive_source_lines:
            if float_is_zero(remaining_qty, precision_rounding=precision):
                break
            source_qty = self._get_source_move_line_quantity(source_line)
            split_qty = min(remaining_qty, source_qty)
            if float_is_zero(split_qty, precision_rounding=precision):
                continue
            vals = self._prepare_return_move_line_vals(
                move, line, source_location, dest_location, split_qty, source_line
            )
            created_lines |= self.env['stock.move.line'].create(vals)
            remaining_qty -= split_qty

        if float_compare(remaining_qty, 0.0, precision_rounding=precision) > 0:
            if line.product_id.tracking != 'none' and source_move_lines:
                raise UserError(
                    '组件 %s 的退库数量 %.6g 超过源批次可分摊数量，已阻止退库以避免退错批次。'
                    % (line.product_id.display_name, quantity_to_return)
                )
            vals = self._prepare_return_move_line_vals(
                move, line, source_location, dest_location, remaining_qty
            )
            created_lines |= self.env['stock.move.line'].create(vals)

        return created_lines

    def action_confirm_return(self):
        """确认返回剩余组件 - 优化版本"""
        self.ensure_one()
        
        valid_lines = self._validate_data()
        self._lock_source_moves(valid_lines)
        self._validate_data()
        
        try:
            validation_action = False
            # 处理每个组件行
            for line in self.component_line_ids:
                if line.return_qty > 0:
                    # 创建返回历史记录
                    history_vals = {
                        'production_id': self.production_id.id,
                        'product_id': line.product_id.id,
                        'source_move_id': line.move_id.id if line.move_id else False,
                        'quantity': line.return_qty,
                        'return_strategy': self.return_strategy,
                        'target_location_id': self.target_location_id.id,
                        'return_reason_id': self.return_reason_id.id if self.return_reason_id else False,
                        'custom_reason': self.custom_reason,
                        'notes': self.notes,
                        'processed_by': self.env.user.id,
                        'processed_date': fields.Datetime.now(),
                    }
                    history = self.env['mrp.production.return.history'].sudo().with_context(
                        from_return_wizard=True
                    ).create(history_vals)

                    # 根据策略处理。只有实际调拨完成后才标记历史为 done。
                    wizard = self.with_company(self.production_id.company_id)
                    if self.return_strategy == 'scrap':
                        current_action = wizard._process_scrap_return(history, line)
                    else:
                        current_action = wizard._process_location_return(history, line)
                    if current_action and not validation_action:
                        validation_action = current_action
                    if history.picking_id and history.picking_id.state == 'done':
                        history.sudo().with_context(from_return_wizard=True).action_done()

            if validation_action:
                return validation_action
            
            # 发送通知
            if self.send_notification:
                self._send_notification()
            
            # 记录日志
            _logger.info(f"[剩余组件返回] 制造订单 {self.production_id.name} 的剩余组件已处理完成")

            if self.complete_production_after_return:
                return self.production_id.with_context(
                    processing_return=True,
                    skip_backorder=True,
                ).button_mark_done()

            title = '处理完成' if self.auto_confirm_picking else '已创建待确认调拨单'
            message = (
                '剩余组件已成功处理。您可以继续生产或手动完成制造订单。'
                if self.auto_confirm_picking else
                '剩余组件调拨单已创建，请确认并完成调拨后再继续。'
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': title,
                    'message': message,
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }
            
        except Exception as e:
            _logger.error(f"[剩余组件返回] 处理失败: {str(e)}")
            raise UserError(f'处理失败: {str(e)}')

    def _get_return_warehouse(self):
        """Return the warehouse that owns the production flow."""
        self.ensure_one()
        warehouse = self.production_id.picking_type_id.warehouse_id
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.production_id.company_id.id)
            ], limit=1)
        if not warehouse:
            raise UserError('无法找到制造订单所属公司的仓库')
        return warehouse

    def _get_return_picking_type(self):
        """Return the internal picking type for the production warehouse."""
        warehouse = self._get_return_warehouse()
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not picking_type:
            raise UserError('无法找到制造订单仓库的内部调拨单类型')
        return picking_type

    def _create_return_transfer(self, history, line, dest_location, origin_prefix):
        """Create and optionally validate the return transfer for one component line."""
        self.ensure_one()
        production = self.production_id
        source_location = production.location_src_id
        if not source_location:
            raise UserError('无法找到制造订单的源位置')
        if dest_location.company_id and dest_location.company_id != production.company_id:
            raise UserError('目标位置必须属于制造订单公司或共享位置')

        picking_type = self._get_return_picking_type()
        company = production.company_id
        picking_vals = {
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': f'{origin_prefix} - {production.name}',
            'note': f'{origin_prefix}\n策略: {dict(self._fields["return_strategy"].selection)[self.return_strategy]}\n原因: {self.return_reason_id.name if self.return_reason_id else self.custom_reason or "无"}',
            'user_id': self.env.user.id,
            'company_id': company.id,
        }
        picking = self.env['stock.picking'].with_company(company).create(picking_vals)

        move_vals = {
            'name': f'{origin_prefix} - {line.product_id.name}',
            'product_id': line.product_id.id,
            'product_uom_qty': line.return_qty,
            'product_uom': line.move_id.product_uom.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'picking_id': picking.id,
            'origin': f'{origin_prefix} - {production.name}',
            'company_id': company.id,
        }
        move = self.env['stock.move'].with_company(company).create(move_vals)

        history.sudo().write({
            'picking_id': picking.id,
            'move_id': move.id,
        })

        if self.auto_confirm_picking:
            picking.action_confirm()
            self._create_return_move_lines_from_source(move, line, source_location, dest_location)
            if picking.state in ('assigned', 'confirmed'):
                result = picking.button_validate()
                if isinstance(result, dict):
                    return result
        return False

    def _process_location_return(self, history, line):
        """处理位置返回"""
        return self._create_return_transfer(
            history,
            line,
            self.target_location_id,
            '制造订单剩余组件返回',
        )

    def _process_scrap_return(self, history, line):
        """处理报废返回 - 转移到报废库位。"""
        if not self.scrap_location_id:
            raise UserError('请选择报废仓库位置')
        return self._create_return_transfer(
            history,
            line,
            self.scrap_location_id,
            '制造订单剩余组件转移至报废库位',
        )

    def _send_notification(self):
        """发送通知"""
        # 这里可以实现邮件或系统通知
        pass
    
    def get_available_product_ids(self):
        """获取可选组件的产品ID列表（用于视图domain）"""
        self.ensure_one()
        if not self.production_id:
            return []
        
        # 获取未处理的剩余组件移动，按 move_id 区分同产品多批次。
        remaining_moves = self._get_unprocessed_remaining_moves(self.production_id)
        
        # 获取当前已添加的组件
        existing_move_ids = self._get_existing_component_move_ids()
        
        # 过滤掉已经添加的组件
        available_moves = remaining_moves.filtered(
            lambda m: m.id not in existing_move_ids
        )
        
        return available_moves.mapped('product_id').ids
    
    @api.onchange('production_id')
    def _onchange_production_id(self):
        """制造订单变更时，更新组件行的可选组件"""
        if self.production_id:
            # 触发组件行的可用产品列表重新计算
            for line in self.component_line_ids:
                line._compute_available_product_ids()
    
    def action_add_available_components(self):
        """添加可用的剩余组件"""
        self.ensure_one()
        if not self.production_id:
            raise UserError('请先选择制造订单')
        
        # 获取未处理的剩余组件移动，按 move_id 区分同产品多批次。
        remaining_moves = self._get_unprocessed_remaining_moves(self.production_id)
        
        # 获取当前已添加的组件
        existing_move_ids = self._get_existing_component_move_ids()
        
        # 过滤掉已经添加的组件
        available_moves = remaining_moves.filtered(
            lambda m: m.id not in existing_move_ids
        )
        
        # 创建新的组件行
        for move in available_moves:
            self.env['mrp.production.return.wizard.line'].create({
                'wizard_id': self.id,
                'move_id': move.id,
                'product_id': move.product_id.id,
                'return_qty': move.product_uom_qty - move.quantity,
            })
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '添加完成',
                'message': f'已添加 {len(available_moves)} 个可用组件',
                'type': 'success',
                'sticky': False,
            }
        }
