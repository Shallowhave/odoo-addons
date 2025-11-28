# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round
import math
import logging

_logger = logging.getLogger(__name__)


class QualityCheck(models.Model):
    _inherit = 'quality.check'
    
    # 测试类型字段
    test_type = fields.Char(
        string='测试类型',
        related='point_id.test_type_id.technical_name',
        readonly=True,
        store=False,
        help='质检点的技术名称，用于判断是否为组件扫码确认测试'
    )
    
    # 选择的待登记组件（用户先选择要验证的组件）
    selected_component_id = fields.Many2one(
        'product.product',
        string='选择的待登记组件',
        help='用户选择的待登记组件，需要验证扫码的产品是否匹配此组件'
    )
    
    # 扫码验证的组件信息
    scanned_component_id = fields.Many2one(
        'product.product',
        string='扫码的组件',
        help='通过扫码验证的组件产品'
    )
    
    scanned_component_code = fields.Char(
        string='扫码的组件编码',
        help='扫码获取的组件编码（条码/批次号）'
    )
    
    component_verification_result = fields.Selection([
        ('pending', '待验证'),
        ('matched', '匹配'),
        ('mismatched', '不匹配'),
    ], string='验证结果', default='pending', readonly=True)
    
    component_verification_message = fields.Text(
        string='验证消息',
        help='组件验证的详细消息'
    )
    
    @api.model_create_multi
    def create(self, vals_list):
        """
        创建质检记录时，如果质检点配置了 component_id，则自动设置到 selected_component_id
        参考 register_consumed_materials 的实现
        支持批量创建（Odoo 18 推荐）
        """
        records = super(QualityCheck, self).create(vals_list)
        
        # 批量处理：如果质检点配置了 component_id，则自动设置到 selected_component_id
        records_to_update = records.filtered(
            lambda r: not r.selected_component_id 
            and r.point_id 
            and r.point_id.component_id 
            and r.point_id.test_type_id.technical_name == 'component_scan_verify'
        )
        if records_to_update:
            for record in records_to_update:
                record.selected_component_id = record.point_id.component_id.id
        
        return records
    
    def _ensure_product_uom_consistency(self, product, move_uom):
        """
        确保产品的 UOM 与移动行的 UOM 一致（辅助方法）
        
        :param product: product.product 记录
        :param move_uom: uom.uom 记录（来自移动行）
        :return: 是否进行了修复
        """
        if not product or not move_uom:
            return False
        
        needs_fix = False
        fix_reason = ''
        
        # 检查产品变体的 UOM
        if not product.uom_id:
            needs_fix = True
            fix_reason = '产品变体没有 UOM'
        elif product.uom_id.category_id != move_uom.category_id:
            needs_fix = True
            fix_reason = '产品变体 UOM 类别不匹配'
        
        # 检查产品模板的 UOM
        tmpl = product.product_tmpl_id
        if tmpl:
            tmpl_uom = tmpl.uom_id
            if not tmpl_uom:
                needs_fix = True
                fix_reason = fix_reason or '产品模板没有 UOM'
            elif tmpl_uom.category_id != move_uom.category_id:
                needs_fix = True
                fix_reason = fix_reason or '产品模板 UOM 类别不匹配'
        
        if needs_fix:
            # 修复产品变体的 UOM
            if not product.uom_id or product.uom_id.category_id != move_uom.category_id:
                product.sudo().write({'uom_id': move_uom.id})
                product.invalidate_recordset(['uom_id'])
            
            # 修复产品模板的 UOM
            if tmpl and (not tmpl.uom_id or tmpl.uom_id.category_id != move_uom.category_id):
                tmpl.sudo().write({'uom_id': move_uom.id})
                tmpl.invalidate_recordset(['uom_id'])
            
            # 刷新数据库
            self.env['product.product'].flush_model(['uom_id'])
            self.env['product.template'].flush_model(['uom_id'])
            
            _logger.warning(
                _("[质检通过] 修复产品 UOM: 产品=%s, 原因=%s, 新UOM=%s"),
                product.name, fix_reason, move_uom.name
            )
            return True
        
        return False
    
    def do_pass(self):
        """
        质检通过时执行组件扫码确认
        """
        # 在调用父类方法之前，确保所有可能被 register_consumed_materials 使用的产品都有正确的 UOM
        if self.production_id:
            # 修复 point_id.component_id 的 UOM
            if self.point_id and self.point_id.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.point_id.component_id.id
                )
                if component_move and component_move[0].product_uom:
                    self._ensure_product_uom_consistency(
                        self.point_id.component_id,
                        component_move[0].product_uom
                    )
            
            # 修复 self.component_id 的 UOM
            if self.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.component_id.id
                )
                if component_move and component_move[0].product_uom:
                    self._ensure_product_uom_consistency(
                        self.component_id,
                        component_move[0].product_uom
                    )
                    
                    # 确保 self.move_id 被正确设置
                    move = component_move[0]
                    if not self.move_id or self.move_id.id != move.id:
                        self.move_id = move.id
                        self.invalidate_recordset(['move_id'])
                    
                    # 确保 move.product_uom 有正确的 rounding
                    move_uom = move.product_uom
                    if move_uom and (not move_uom.rounding or move_uom.rounding <= 0):
                        move_uom.sudo().write({'rounding': 0.01})
                        move_uom.invalidate_recordset(['rounding'])
                        self.env['uom.uom'].flush_model(['rounding'])
        
        # 如果是组件扫码确认类型的质检，执行验证
        if self.test_type == 'component_scan_verify':
            # 如果质检点配置了待登记组件，则自动使用配置的组件
            if self.point_id and self.point_id.component_id and not self.selected_component_id:
                self.selected_component_id = self.point_id.component_id
            
            # 检查是否已选择待登记组件
            if not self.selected_component_id:
                raise UserError(_('请先选择待登记的组件！'))
            
            # 检查是否已经验证过
            if not self.scanned_component_id:
                raise UserError(_('请先扫码确认组件！'))
            
            # 检查验证结果
            if self.component_verification_result != 'matched':
                raise UserError(_('组件验证失败，无法通过质检！\n%s') % (
                    self.component_verification_message or _('请确保扫码的组件匹配选中的待登记组件')
                ))
            
            # 确保组件的计量单位与生产订单移动行中的单位一致
            if self.production_id and self.selected_component_id:
                # 确保 Odoo 原生的 component_id 字段也被设置
                if not self.component_id:
                    self.component_id = self.selected_component_id.id
                
                # 查找生产订单中该组件的移动行
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.selected_component_id.id
                )
                
                if component_move and component_move[0].product_uom:
                    move = component_move[0]
                    component_product = self.selected_component_id
                    
                    # 检查并修复 UOM
                    if self._ensure_product_uom_consistency(component_product, move.product_uom):
                        # 重新加载产品记录
                        component_product = self.env['product.product'].browse(component_product.id)
                    
                    # 确保 component_id 也使用更新后的产品
                    if self.component_id != component_product.id:
                        self.component_id = component_product.id
            
            # 在调用父类方法之前，强制刷新环境并重新加载所有相关记录
            if self.component_id:
                self.component_id.invalidate_recordset(['uom_id'])
                if self.component_id.product_tmpl_id:
                    self.component_id.product_tmpl_id.invalidate_recordset(['uom_id'])
                
                # 重新从数据库加载
                fresh_component = self.env['product.product'].browse(self.component_id.id)
                fresh_template = fresh_component.product_tmpl_id
                
                # 如果产品模板没有单位，也设置一下
                if fresh_template and not fresh_template.uom_id and fresh_component.uom_id:
                    fresh_template.sudo().write({'uom_id': fresh_component.uom_id.id})
                    fresh_template.invalidate_recordset(['uom_id'])
                
                # 确保 self.component_id 使用最新的记录
                self.component_id = fresh_component
        
        # 调用父类方法执行质检通过
        res = super(QualityCheck, self).do_pass()
        
        return res
    
    def _create_extra_move_lines(self):
        """
        覆盖父类方法，确保 move_id.product_uom 正确
        """
        if self.production_id and self.component_id and self.move_id:
            # 如果 move_id.product_uom 是 False，从生产订单移动行获取
            if not self.move_id.product_uom:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.component_id.id
                )
                if component_move and component_move[0].product_uom:
                    move = component_move[0]
                    self.move_id.sudo().write({'product_uom': move.product_uom.id})
                    self.move_id.invalidate_recordset(['product_uom'])
                    self.env['stock.move'].flush_model(['product_uom'])
                    self.move_id = self.env['stock.move'].browse(self.move_id.id)
                    _logger.warning(
                        _("[创建额外移动行] 修复 move_id %s 的 product_uom: None -> %s"),
                        self.move_id.name, move.product_uom.name
                    )
            
            # 确保 move.product_uom 的 rounding 有效
            if self.move_id.product_uom:
                move_uom = self.move_id.product_uom
                if not move_uom.rounding or move_uom.rounding <= 0:
                    move_uom.sudo().write({'rounding': 0.01})
                    move_uom.invalidate_recordset(['rounding'])
                    self.env['uom.uom'].flush_model(['rounding'])
        
        return super(QualityCheck, self)._create_extra_move_lines()
    
    def get_configured_component(self):
        """
        获取质检点配置的待登记组件
        
        此方法由前端调用，用于获取质检点配置的组件
        参考 register_consumed_materials 的实现，使用 component_id 字段
        
        :return: 组件产品信息字典，如果没有配置则返回 False
        """
        if not self.point_id:
            return False
        
        # 使用 Odoo 原生的 component_id 字段
        if self.point_id.component_id:
            return {
                'id': self.point_id.component_id.id,
                'name': self.point_id.component_id.name,
                'code': self.point_id.component_id.default_code or '',
            }
        
        return False
    
    def verify_component(self, scanned_component_id=None):
        """
        验证扫码的组件是否匹配选中的待登记组件
        
        此方法由前端调用，用于实时验证
        验证逻辑：
        1. 检查是否已选择待登记组件
        2. 验证扫码的组件是否匹配选中的待登记组件
        3. 记录验证结果
        
        :param scanned_component_id: 扫码的组件ID（前端传递）
        """
        if not self.production_id:
            raise UserError(_('无法获取生产订单信息，请确保质检点关联了生产订单！'))
        
        if not self.selected_component_id:
            raise UserError(_('请先选择待登记的组件！'))
        
        # 如果前端传递了扫码的组件ID，先设置到记录中
        if scanned_component_id:
            self.scanned_component_id = scanned_component_id
        
        if not self.scanned_component_id:
            raise UserError(_('请先扫码确认组件！'))
        
        production = self.production_id
        selected_product = self.selected_component_id
        scanned_product = self.scanned_component_id
        
        # 验证扫码的组件是否匹配选中的待登记组件
        if scanned_product.id != selected_product.id:
            # 组件不匹配
            self.component_verification_result = 'mismatched'
            self.component_verification_message = _(
                '组件不匹配！\n'
                '选择的待登记组件：%s (%s)\n'
                '扫码的组件：%s (%s)'
            ) % (
                selected_product.name,
                selected_product.default_code or '',
                scanned_product.name,
                scanned_product.default_code or ''
            )
            
            # 记录日志（跳过邮件发送，避免未配置邮件时报错）
            try:
                self.with_context(mail_notrack=True).message_post(
                    body=_('组件验证失败<br/>选择的待登记组件：%s (%s)<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                        selected_product.name,
                        selected_product.default_code or '',
                        scanned_product.name,
                        scanned_product.default_code or '',
                        production.name
                    )
                )
            except Exception as e:
                # 如果 message_post 失败（例如未配置邮件），只记录日志，不抛出异常
                _logger.warning(
                    _("[组件扫码确认] 记录验证失败消息时出错（已跳过）: %s"),
                    str(e)
                )
            
            _logger.warning(
                _("[组件扫码确认] 验证失败: 质检ID=%s, 选择的组件=%s(ID:%s), 扫码的组件=%s(ID:%s), 生产订单=%s"),
                self.id, 
                selected_product.name, selected_product.id,
                scanned_product.name, scanned_product.id,
                production.name
            )
            
            return {'success': False, 'message': self.component_verification_message}
        
        # 组件匹配
        self.component_verification_result = 'matched'
        self.component_verification_message = _(
            '组件验证成功！\n'
            '选择的待登记组件：%s (%s)\n'
            '扫码的组件：%s (%s)\n'
            '匹配成功！'
        ) % (
            selected_product.name,
            selected_product.default_code or '',
            scanned_product.name,
            scanned_product.default_code or ''
        )
        
        # 记录日志（跳过邮件发送，避免未配置邮件时报错）
        try:
            self.with_context(mail_notrack=True).message_post(
                body=_('组件验证成功<br/>选择的待登记组件：%s (%s)<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                    selected_product.name,
                    selected_product.default_code or '',
                    scanned_product.name,
                    scanned_product.default_code or '',
                    production.name
                )
            )
        except Exception as e:
            # 如果 message_post 失败（例如未配置邮件），只记录日志，不抛出异常
            _logger.warning(
                _("[组件扫码确认] 记录验证成功消息时出错（已跳过）: %s"),
                str(e)
            )
        
        _logger.info(
            _("[组件扫码确认] 验证成功: 质检ID=%s, 选择的组件=%s, 扫码的组件=%s, 生产订单=%s"),
            self.id, selected_product.name, scanned_product.name, production.name
        )
        
        # 验证成功后，自动通过质检并结束作业
        # 确保 component_id 字段被设置（register_consumed_materials 需要）
        if not self.component_id:
            self.component_id = selected_product.id
        
        # 自动调用 do_pass() 通过质检
        try:
            self.do_pass()
            _logger.info(
                _("[组件扫码确认] 自动通过质检: 质检ID=%s, 组件=%s"),
                self.id, selected_product.name
            )
            return {
                'success': True, 
                'message': self.component_verification_message,
                'auto_passed': True  # 标记已自动通过
            }
        except Exception as e:
            _logger.error(
                _("[组件扫码确认] 自动通过质检失败: 质检ID=%s, 错误=%s"),
                self.id, str(e)
            )
            # 即使自动通过失败，也返回验证成功，让用户可以手动点击验证按钮
            return {
                'success': True, 
                'message': self.component_verification_message + '\n' + _('请手动点击验证按钮完成质检。'),
                'auto_passed': False
            }
    
    def action_scan_component(self):
        """
        扫码组件（前端调用）
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'component_scan_verify',
            'context': {
                'quality_check_id': self.id,
            }
        }
    
    def _update_component_quantity(self):
        """
        更新组件数量
        
        修复：当产品的 UOM rounding 为 0 时，使用默认精度值 0.01
        避免 AssertionError: precision_rounding must be positive, got 0.0
        """
        self.ensure_one()
        
        default_rounding = 0.01
        
        # 修复 move_id.product_uom 的 rounding（如果存在且为 0）
        if hasattr(self, 'move_id') and self.move_id and self.move_id.product_uom:
            move_uom = self.move_id.product_uom
            if not move_uom.rounding or move_uom.rounding <= 0:
                move_uom.sudo().write({'rounding': default_rounding})
                move_uom.invalidate_recordset(['rounding'])
                self.env['uom.uom'].flush_model(['rounding'])
                self.invalidate_recordset(['move_id'])
                self.move_id = self.env['stock.move'].browse(self.move_id.id)
                _logger.warning(
                    _("[组件数量更新] 修复 move_id.product_uom %s rounding 为 %s"),
                    move_uom.name, default_rounding
                )
        
        # 修复组件产品的 UOM rounding（如果存在且为 0）
        if self.component_id and self.component_id.uom_id:
            if not self.component_id.uom_id.rounding or self.component_id.uom_id.rounding <= 0:
                self.component_id.uom_id.sudo().write({'rounding': default_rounding})
                self.component_id.uom_id.invalidate_recordset(['rounding'])
                self.env['uom.uom'].flush_model(['rounding'])
        
        # 如果 move_id 不存在，尝试从生产订单移动行获取
        if not hasattr(self, 'move_id') or not self.move_id:
            if self.production_id and self.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.component_id.id
                )
                if component_move:
                    self.move_id = component_move[0].id
                    self.invalidate_recordset(['move_id'])
        
        # 如果仍然没有 move_id，调用父类方法
        if not self.move_id:
            return super(QualityCheck, self)._update_component_quantity()
        
        # 重新加载 move，确保获取最新值
        move = self.env['stock.move'].browse(self.move_id.id)
        
        # 确保 move.product_uom 存在且 rounding 正确
        if not move.product_uom:
            # 如果 move 没有 product_uom，从生产订单移动行获取
            if self.production_id and self.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.component_id.id
                )
                if component_move and component_move[0].product_uom:
                    move.sudo().write({'product_uom': component_move[0].product_uom.id})
                    move.invalidate_recordset(['product_uom'])
                    self.env['stock.move'].flush_model(['product_uom'])
                    move = self.env['stock.move'].browse(move.id)
                    _logger.warning(
                        _("[组件数量更新] 为 move %s 设置 product_uom=%s"),
                        move.name, component_move[0].product_uom.name
                    )
        
        # 如果仍然没有 product_uom，调用父类方法
        if not move.product_uom:
            return super(QualityCheck, self)._update_component_quantity()
        
        # 确保 product_uom 的 rounding 正确
        move_uom = move.product_uom
        if not move_uom.rounding or move_uom.rounding <= 0:
            move_uom.sudo().write({'rounding': default_rounding})
            move_uom.invalidate_recordset(['rounding'])
            self.env['uom.uom'].flush_model(['rounding'])
            move = self.env['stock.move'].browse(move.id)
            move_uom = move.product_uom
            _logger.warning(
                _("[组件数量更新] 修复 move %s 的 UOM %s rounding 为 %s"),
                move.name, move_uom.name, default_rounding
            )
        
        # 确保 rounding 有效
        rounding = move_uom.rounding or default_rounding
        if rounding <= 0:
            rounding = default_rounding
        
        # 在调用父类方法之前，确保 self.move_id 使用最新的 move
        self.move_id = move
        self.invalidate_recordset(['move_id'])
        self.env['stock.move'].flush_model(['product_uom'])
        self.env['uom.uom'].flush_model(['rounding'])
        
        # 如果是序列号组件，使用父类方法（但已经修复了 rounding）
        if self.component_tracking == 'serial':
            return super(QualityCheck, self)._update_component_quantity()
        
        # 计算新的数量（使用父类方法中的逻辑）
        try:
            new_qty = self._prepare_component_quantity(move, self.workorder_id.qty_producing)
        except Exception as e:
            _logger.error(_("[组件数量更新] 计算新数量失败: %s"), str(e))
            return super(QualityCheck, self)._update_component_quantity()
        
        # 使用向下取整而不是四舍五入，避免 0.255 被四舍五入到 0.26
        # 先向下取整到 rounding 的倍数，保留原始精度
        if rounding > 0:
            # 向下取整：将数量向下取整到 rounding 的倍数
            qty_todo = math.floor(new_qty / rounding) * rounding
        else:
            qty_todo = new_qty
        
        # 如果 move 已拣选且质检状态不是通过，需要减去已拣选的数量
        if (move.picked and self.quality_state != 'pass'):
            qty_todo = qty_todo - move.quantity
        
        # 如果有 move_line_id 和 lot_id，取最小值
        if self.move_line_id and self.move_line_id.lot_id:
            qty_todo = min(self.move_line_id.quantity, qty_todo)
        
        # 设置 qty_done
        self.qty_done = qty_todo
        
        return True
