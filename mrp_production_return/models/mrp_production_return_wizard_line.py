# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class MrpProductionReturnWizardLine(models.TransientModel):
    _name = 'mrp.production.return.wizard.line'
    _description = '制造订单剩余组件返回向导行'

    wizard_id = fields.Many2one(
        'mrp.production.return.wizard',
        string='向导',
        required=True,
        ondelete='cascade'
    )
    move_id = fields.Many2one(
        'stock.move',
        string='库存移动',
        required=False  # 改为非必需，因为选择产品后才设置
        # 注意：不在模型中设置 readonly，只在视图中设置
        # 这样 default_get 创建记录时才能正常传递 move_id
    )
    
    # 可用产品列表（用于 domain 过滤）
    available_product_ids = fields.Many2many(
        'product.product',
        string='可用产品',
        compute='_compute_available_product_ids',
        help='可用于选择的产品列表（仅剩余组件）'
    )
    available_product_ids_str = fields.Char(
        string='可用产品ID列表（字符串）',
        compute='_compute_available_product_ids',
        help='用于日志记录'
    )
    available_product_ids_for_domain = fields.Char(
        string='可用产品ID列表（用于domain）',
        compute='_compute_available_product_ids',
        help='用于生成 domain'
    )
    
    product_id = fields.Many2one(
        'product.product',
        string='组件',
        required=True,
        help='选择要退库的组件'
    )
    
    @api.onchange('wizard_id')
    def _onchange_wizard_id(self):
        """当 wizard_id 变化时，触发 domain 更新（确保在字段打开时也能应用 domain）"""
        return self._onchange_wizard_lines()
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='计量单位',
        related='move_id.product_uom',
        readonly=True
    )
    expected_qty = fields.Float(
        string='计划数量',
        related='move_id.product_uom_qty',
        readonly=True
    )
    consumed_qty = fields.Float(
        string='已消耗数量',
        related='move_id.quantity',
        readonly=True
    )
    remaining_qty = fields.Float(
        string='剩余数量',
        compute='_compute_remaining_qty',
        readonly=True
    )
    return_qty = fields.Float(
        string='返回数量',
        required=True,
        help='要返回的组件数量'
    )
    
    # 附加单位字段（原始值）
    lot_quantity_total = fields.Float(
        string='计划附加单位数量',
        related='move_id.lot_quantity',
        readonly=True,
        help='该组件的计划附加单位数量（总计）'
    )
    lot_unit_name = fields.Char(
        string='附加单位',
        compute='_compute_lot_unit_info',
        readonly=True,
        store=False,
        help='该组件的附加单位名称'
    )
    # 剩余附加单位数量（按比例计算）
    lot_quantity = fields.Float(
        string='剩余附加单位数量',
        compute='_compute_lot_quantity_remaining',
        readonly=True,
        store=False,
        help='该组件的剩余附加单位数量（按比例计算）'
    )

    @api.depends('expected_qty', 'consumed_qty')
    def _compute_remaining_qty(self):
        """计算剩余数量"""
        for record in self:
            record.remaining_qty = record.expected_qty - record.consumed_qty
    
    @api.depends('move_id', 'move_id.lot_quantity', 'move_id.lot_unit_name', 'expected_qty', 'remaining_qty')
    def _compute_lot_quantity_remaining(self):
        """计算剩余附加单位数量（按比例计算）"""
        for record in self:
            if not record.move_id:
                record.lot_quantity = 0.0
                continue
            
            # 获取计划的附加单位数量
            total_lot_quantity = record.move_id.lot_quantity or 0.0
            expected_qty = record.expected_qty or 0.0
            remaining_qty = record.remaining_qty or 0.0
            
            # 如果计划数量为0，无法按比例计算
            if expected_qty == 0:
                record.lot_quantity = 0.0
                continue
            
            # 按比例计算剩余附加单位数量
            if total_lot_quantity > 0:
                ratio = remaining_qty / expected_qty
                record.lot_quantity = total_lot_quantity * ratio
            else:
                record.lot_quantity = 0.0
    
    @api.depends('move_id', 'move_id.lot_unit_name')
    def _compute_lot_unit_info(self):
        """计算附加单位名称"""
        for record in self:
            if record.move_id and record.move_id.lot_unit_name:
                record.lot_unit_name = record.move_id.lot_unit_name
            else:
                # 尝试从 move_line_ids 获取
                if record.move_id and record.move_id.move_line_ids:
                    unit_names = record.move_id.move_line_ids.mapped('lot_unit_name')
                    record.lot_unit_name = next((name for name in unit_names if name and name != 'custom'), '')
                    # 如果是 'custom'，尝试获取自定义单位名称
                    if not record.lot_unit_name:
                        custom_names = record.move_id.move_line_ids.mapped('lot_unit_name_custom')
                        record.lot_unit_name = next((name for name in custom_names if name), '')
                else:
                    record.lot_unit_name = ''

    @api.depends('wizard_id', 'wizard_id.production_id', 'wizard_id.component_line_ids', 'wizard_id.component_line_ids.product_id', 'product_id')
    def _compute_available_product_ids(self):
        """计算可用产品列表（仅剩余组件，排除已添加的组件）"""
        _logger.info(f"[向导行] _compute_available_product_ids 开始: 处理 {len(self)} 条记录")
        
        for record in self:
            try:
                _logger.info(
                    f"[向导行] _compute_available_product_ids: 处理记录 ID={record.id}, "
                    f"wizard_id={record.wizard_id.id if record.wizard_id else None}, "
                    f"production_id={record.wizard_id.production_id.id if record.wizard_id and record.wizard_id.production_id else None}"
                )
                
                if not record.wizard_id:
                    _logger.warning(f"[向导行] wizard_id 为空，设置空记录集")
                    record.available_product_ids = record.env['product.product']
                    record.available_product_ids_str = ''
                    record.available_product_ids_for_domain = '[0]'
                    continue
                
                wizard = record.wizard_id
                
                # 检查是否是 NewId（临时ID）
                is_new_id = hasattr(wizard.id, '__class__') and 'NewId' in str(type(wizard.id))
                
                production = None
                if wizard.production_id:
                    production = wizard.production_id
                elif is_new_id:
                    _logger.warning(f"[向导行] wizard_id 是 NewId，尝试从多个来源获取 production_id")
                    try:
                        if 'default_production_id' in record.env.context:
                            production_id = record.env.context.get('default_production_id')
                            if production_id:
                                production = record.env['mrp.production'].browse(production_id)
                                _logger.info(f"[向导行] 方法1: 从 context default_production_id 获取 production_id={production.id}")
                        
                        if not production and record.env.context.get('active_model') == 'mrp.production':
                            production_id = record.env.context.get('active_id')
                            if production_id:
                                production = record.env['mrp.production'].browse(production_id)
                                _logger.info(f"[向导行] 方法2: 从 context active_id 获取 production_id={production.id}")
                        
                        if not production and hasattr(wizard, '_origin') and wizard._origin:
                            try:
                                if wizard._origin.production_id:
                                    production = wizard._origin.production_id
                                    _logger.info(f"[向导行] 方法3: 从 wizard._origin 获取 production_id={production.id}")
                            except Exception:
                                pass
                        
                        if not production:
                            try:
                                wizard._read(['production_id'])
                                if wizard.production_id:
                                    production = wizard.production_id
                                    _logger.info(f"[向导行] 方法4: 通过 _read 获取 production_id={production.id}")
                            except Exception:
                                pass
                                
                    except Exception as e:
                        _logger.error(f"[向导行] 获取 production_id 时出错: {str(e)}", exc_info=True)
                else:
                    try:
                        if isinstance(wizard.id, int):
                            wizard = record.env['mrp.production.return.wizard'].browse(wizard.id)
                            if wizard.production_id:
                                production = wizard.production_id
                                _logger.info(f"[向导行] 重新加载 wizard: production_id={production.id}")
                    except Exception as e:
                        _logger.error(f"[向导行] 重新加载 wizard 时出错: {str(e)}", exc_info=True)
                
                if not production:
                    _logger.warning(f"[向导行] 无法获取 production_id，设置空记录集")
                    record.available_product_ids = record.env['product.product']
                    record.available_product_ids_str = ''
                    record.available_product_ids_for_domain = '[0]'
                    continue
                
                _logger.info(f"[向导行] 制造订单: {production.name}(ID:{production.id})")
                
                # 获取剩余组件移动记录
                remaining_moves = production.move_raw_ids.filtered(
                    lambda m: m.state in ('done', 'assigned', 'partially_available') and m.product_uom_qty > m.quantity
                )
                _logger.info(f"[向导行] 剩余组件移动记录数: {len(remaining_moves)}")
                
                # 排除已处理过的产品
                processed_history = record.env['mrp.production.return.history'].search([
                    ('production_id', '=', production.id)
                ])
                processed_products = processed_history.mapped('product_id')
                _logger.info(f"[向导行] 已处理过的产品数: {len(processed_products)}")
                
                if processed_products:
                    remaining_moves = remaining_moves.filtered(
                        lambda m: m.product_id not in processed_products
                    )
                    _logger.info(f"[向导行] 过滤已处理产品后，剩余移动记录数: {len(remaining_moves)}")
                
                # 获取当前已添加的组件（排除当前记录自己）
                # 关键：使用 exists() 确保只获取真实存在的记录，排除已删除的记录
                all_lines = record.wizard_id.component_line_ids.exists()
                existing_lines = all_lines.filtered(
                    lambda l: l.id != record.id and l.product_id and l.exists()
                )
                existing_product_ids = existing_lines.mapped('product_id').ids if existing_lines else []
                _logger.info(
                    f"[向导行] 当前已添加的组件数: {len(existing_lines)}, "
                    f"已添加组件ID列表: {existing_product_ids}"
                )
                
                # 过滤掉已经添加的组件
                available_moves = remaining_moves.filtered(
                    lambda m: m.product_id.id not in existing_product_ids
                )
                _logger.info(f"[向导行] 过滤已添加组件后，可用移动记录数: {len(available_moves)}")
                
                # 获取可用产品
                available_products = available_moves.mapped('product_id')
                product_ids = available_products.ids
                _logger.info(
                    f"[向导行] 可用产品数: {len(available_products)}, "
                    f"可用产品ID列表: {product_ids}"
                )
                
                # 设置可用产品列表（Many2many 字段）
                record.available_product_ids = available_products
                
                # 生成ID列表字符串用于domain
                record.available_product_ids_str = ','.join(map(str, product_ids)) if product_ids else ''
                
                # 生成用于domain的ID列表（确保即使列表为空，也返回一个有效的格式）
                domain_ids = product_ids if product_ids else [0]
                record.available_product_ids_for_domain = str(domain_ids)
                
                _logger.info(
                    f"[向导行] _compute_available_product_ids 完成: "
                    f"record_id={record.id}, "
                    f"已添加组件数量={len(existing_product_ids)}, "
                    f"可用组件数量={len(available_products)}, "
                    f"可用组件ID列表={product_ids}, "
                    f"domain_ids={domain_ids}"
                )
                
            except Exception as e:
                _logger.error(
                    f"[向导行] _compute_available_product_ids 错误: record_id={record.id}, "
                    f"错误={str(e)}", exc_info=True
                )
                record.available_product_ids = record.env['product.product']
                record.available_product_ids_str = ''
                record.available_product_ids_for_domain = '[0]'

    def _get_available_product_ids(self):
        """获取可用产品ID列表（辅助方法）"""
        try:
            # 确保计算字段已计算
            self._compute_available_product_ids()
            return self.available_product_ids.ids if self.available_product_ids else []
        except Exception:
            return []

    def _get_product_id_domain(self):
        """获取 product_id 字段的 domain"""
        try:
            available_ids = self._get_available_product_ids()
            # 确保 domain 总是有效的：如果列表为空，使用 [0] 作为占位符
            if not available_ids:
                available_ids = [0]
            domain = [('id', 'in', available_ids)]
            _logger.info(
                f"[向导行] _get_product_id_domain: record_id={self.id}, "
                f"available_ids={available_ids}, domain={domain}"
            )
            return domain
        except Exception as e:
            _logger.error(
                f"[向导行] _get_product_id_domain 错误: record_id={self.id}, "
                f"错误={str(e)}", exc_info=True
            )
            return [('id', 'in', [0])]

    @api.onchange('wizard_id', 'wizard_id.component_line_ids')
    def _onchange_wizard_lines(self):
        """当向导的组件行变化时（包括删除），重新计算可用产品列表并更新 domain"""
        _logger.info(
            f"[向导行] _onchange_wizard_lines 被调用: line_id={self.id}, "
            f"wizard_id={self.wizard_id.id if self.wizard_id else None}"
        )
        try:
            if not self.wizard_id:
                _logger.warning(f"[向导行] _onchange_wizard_lines: wizard_id 为空，返回空 domain")
                return {
                    'domain': {'product_id': [('id', '=', False)]}
                }
            
            # 重新计算可用产品列表
            self._compute_available_product_ids()
            available_ids = self._get_available_product_ids()
            
            # 确保 domain 总是有效的
            if not available_ids:
                available_ids = [0]
            
            domain = [('id', 'in', available_ids)]
            _logger.info(
                f"[向导行] _onchange_wizard_lines: 返回 domain={domain}, "
                f"available_ids={available_ids}"
            )
            return {
                'domain': {'product_id': domain}
            }
        except Exception as e:
            _logger.error(
                f"[向导行] _onchange_wizard_lines 错误: line_id={self.id}, "
                f"错误={str(e)}", exc_info=True
            )
            return {
                'domain': {'product_id': [('id', '=', False)]}
            }

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """选择组件时的处理"""
        _logger.info(
            f"[向导行] _onchange_product_id 被调用: "
            f"wizard_line_id={self.id}, product_id={self.product_id.id if self.product_id else None}, "
            f"move_id={self.move_id.id if self.move_id else None}, "
            f"wizard_id={self.wizard_id.id if self.wizard_id else None}"
        )
        
        # 首先更新 domain（确保 domain 总是最新的）
        # 先触发 wizard_lines 的 onchange 来更新可用产品列表
        wizard_result = self._onchange_wizard_lines()
        domain = wizard_result.get('domain', {}).get('product_id', [('id', 'in', [0])])
        
        result = {
            'domain': {'product_id': domain}
        }
        
        if not self.product_id:
            _logger.info("[向导行] product_id 为空，清空 move_id 和 return_qty")
            self.move_id = False
            self.return_qty = 0.0
            return result
        
        if not self.wizard_id or not self.wizard_id.production_id:
            _logger.warning("[向导行] wizard_id 或 production_id 为空，无法处理")
            self.move_id = False
            self.return_qty = 0.0
            return result
        
        production = self.wizard_id.production_id
        _logger.info(f"[向导行] 制造订单: {production.name}(ID:{production.id})")
        
        # 验证是否在可选列表中
        _logger.info(f"[向导行] 可选组件ID列表: {available_ids}")
        
        if available_ids != [0] and self.product_id.id not in available_ids:
            # 如果不在可选列表中，清空选择
            _logger.warning(f"[向导行] 产品 {self.product_id.name}(ID:{self.product_id.id}) 不在可选列表中")
            self.product_id = False
            self.move_id = False
            self.return_qty = 0.0
            return {
                'warning': {
                    'title': '无效选择',
                    'message': '该组件不在可选列表中，请从剩余组件中选择'
                },
                'domain': {'product_id': domain}
            }
        
        # 查找对应的移动记录
        move = production.move_raw_ids.filtered(
            lambda m: m.product_id == self.product_id 
            and m.state in ('done', 'assigned', 'partially_available')
        )
        
        _logger.info(
            f"[向导行] 查找移动记录: 找到 {len(move)} 条记录, "
            f"产品={self.product_id.name}, 制造订单={production.name}"
        )
        
        if not move:
            _logger.error(
                f"[向导行] 未找到移动记录: 产品={self.product_id.name}(ID:{self.product_id.id}), "
                f"制造订单={production.name}(ID:{production.id})"
            )
            self.move_id = False
            self.return_qty = 0.0
            return {
                'warning': {
                    'title': '未找到移动记录',
                    'message': f'无法找到组件 {self.product_id.name} 在制造订单 {production.name} 中的库存移动记录'
                },
                'domain': {'product_id': domain}
            }
        
        # 如果有多个移动记录，使用第一个
        if len(move) > 1:
            _logger.warning(f"[向导行] 找到多条移动记录，使用第一条: {move[0].id}")
            move = move[0]
        
        self.move_id = move
        _logger.info(
            f"[向导行] 设置 move_id={move.id}, "
            f"计划数量={move.product_uom_qty}, 已消耗={move.quantity}, "
            f"单位={move.product_uom.name if move.product_uom else 'None'}"
        )
        
        # 检查是否有剩余数量
        remaining = move.product_uom_qty - move.quantity
        _logger.info(f"[向导行] 计算剩余数量: {remaining} (计划:{move.product_uom_qty} - 已消耗:{move.quantity})")
        
        if remaining <= 0:
            _logger.warning(f"[向导行] 剩余数量 <= 0: {remaining}")
            return {
                'warning': {
                    'title': '无效选择',
                    'message': f'该组件没有剩余数量可以返回（剩余：{remaining}）'
                },
                'domain': {'product_id': domain}
            }
        
        # 自动设置返回数量为剩余数量
        self.return_qty = remaining
        _logger.info(f"[向导行] 设置 return_qty={remaining}")
        
        return result

    def unlink(self):
        """删除行时，触发其他行的重新计算"""
        wizard = self.mapped('wizard_id')
        # 在删除前记录要删除的产品ID
        deleted_product_ids = self.mapped('product_id').ids if self.product_id else []
        _logger.info(f"[向导行] unlink: 删除 {len(self)} 行，删除的产品ID: {deleted_product_ids}")
        
        result = super().unlink()
        
        if wizard:
            # 删除后，其他行的可用产品列表应该包含被删除的产品
            # 触发其他行的重新计算
            remaining_lines = wizard.component_line_ids.exists()
            _logger.info(f"[向导行] unlink: 删除后剩余行数: {len(remaining_lines)}")
            for line in remaining_lines:
                try:
                    # 重新计算可用产品列表
                    line._compute_available_product_ids()
                    _logger.info(
                        f"[向导行] unlink: 重新计算行 {line.id} 的可用产品，"
                        f"可用产品ID: {line.available_product_ids.ids if line.available_product_ids else []}"
                    )
                except Exception as e:
                    _logger.error(f"[向导行] unlink 后重新计算时出错: {str(e)}", exc_info=True)
        return result

    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super().default_get(fields_list)
        if 'return_qty' in fields_list:
            res['return_qty'] = res.get('remaining_qty', 0)
        return res
    
    @api.constrains('return_qty', 'remaining_qty')
    def _check_return_qty(self):
        """验证返回数量"""
        for record in self:
            # 检查负数
            if record.return_qty < 0:
                raise ValidationError(
                    f'组件 {record.product_id.name} 的返回数量不能为负数！\n'
                    f'当前输入：{record.return_qty}'
                )
            
            # 检查是否超过剩余数量（允许小的浮点误差）
            if record.return_qty > record.remaining_qty + 0.0001:
                raise ValidationError(
                    f'组件 {record.product_id.name} 的返回数量不能超过剩余数量！\n'
                    f'剩余数量：{record.remaining_qty} {record.product_uom_id.name}\n'
                    f'您输入的返回数量：{record.return_qty} {record.product_uom_id.name}\n'
                    f'请修改为不超过 {record.remaining_qty} 的值。'
                )
    
    @api.onchange('return_qty')
    def _onchange_return_qty(self):
        """返回数量变更时的实时提示"""
        if self.return_qty and self.remaining_qty:
            if self.return_qty < 0:
                return {
                    'warning': {
                        'title': '数量错误',
                        'message': '返回数量不能为负数！'
                    }
                }
            if self.return_qty > self.remaining_qty + 0.0001:
                return {
                    'warning': {
                        'title': '数量超限',
                        'message': (
                            f'返回数量 {self.return_qty} 超过剩余数量 {self.remaining_qty}！\n'
                            f'最大可返回：{self.remaining_qty} {self.product_uom_id.name}'
                        )
                    }
                }
    
    @api.model_create_multi
    def create(self, vals_list):
        """重写创建方法，确保创建后触发计算"""
        records = super().create(vals_list)
        # 创建后触发计算，确保 available_product_ids 正确
        for record in records:
            record._compute_available_product_ids()
        return records
