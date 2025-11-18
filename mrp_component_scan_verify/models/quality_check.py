# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import UserError
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
        # 先调用父类方法，让 Odoo 原生逻辑先执行（包括从 point.component_id 复制到 check.component_id）
        records = super(QualityCheck, self).create(vals_list)
        
        # 批量处理：如果质检点配置了 component_id，则自动设置到 selected_component_id
        # 注意：这里需要在创建后设置，因为需要先有记录才能设置 Many2one 字段
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
    
    def do_pass(self):
        """
        质检通过时执行组件扫码确认
        """
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
            
            # **关键修复**：确保组件的计量单位与生产订单移动行中的单位一致
            # 这可以避免 register_consumed_materials 时的单位类别不匹配错误
            if self.production_id and self.selected_component_id:
                # **重要**：确保 Odoo 原生的 component_id 字段也被设置
                # 因为 register_consumed_materials 可能使用这个字段
                if not self.component_id:
                    self.component_id = self.selected_component_id.id
                
                # 查找生产订单中该组件的移动行
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.selected_component_id.id
                )
                
                if component_move:
                    # 使用第一个匹配的移动行
                    move = component_move[0]
                    component_product = self.selected_component_id
                    
                    # 添加详细日志
                    _logger.info(
                        _("[组件扫码确认] 开始检查单位一致性: 组件=%s, 移动行单位=%s"),
                        component_product.name,
                        move.product_uom.name if move.product_uom else 'None'
                    )
                    
                    # 检查移动行是否有计量单位
                    if not move.product_uom:
                        _logger.warning(
                            _("[组件扫码确认] 生产订单移动行中没有计量单位，无法验证单位一致性")
                        )
                    else:
                        # 检查产品是否有计量单位
                        current_uom = component_product.uom_id
                        needs_update = False
                        
                        _logger.info(
                            _("[组件扫码确认] 产品当前单位: %s, 移动行单位: %s"),
                            current_uom.name if current_uom else 'None',
                            move.product_uom.name
                        )
                        
                        if not current_uom:
                            # 如果产品没有计量单位，使用移动行中的单位
                            needs_update = True
                            old_uom_name = 'None'
                            _logger.info(_("[组件扫码确认] 产品没有计量单位，需要更新"))
                        elif current_uom.category_id != move.product_uom.category_id:
                            # 如果产品有计量单位，检查是否与移动行中的单位类别一致
                            needs_update = True
                            old_uom_name = current_uom.name
                            _logger.warning(
                                _("[组件扫码确认] 单位类别不匹配: 产品单位类别=%s, 移动行单位类别=%s"),
                                current_uom.category_id.name,
                                move.product_uom.category_id.name
                            )
                        else:
                            # 单位类别一致，不需要更新
                            old_uom_name = current_uom.name
                            _logger.info(_("[组件扫码确认] 单位类别一致，无需更新"))
                        
                        if needs_update:
                            # **关键修复**：同时更新产品变体和产品模板的计量单位
                            # 因为 register_consumed_materials 可能从产品模板读取单位
                            component_product.sudo().write({'uom_id': move.product_uom.id})
                            
                            # 同时更新产品模板的计量单位
                            if component_product.product_tmpl_id:
                                component_product.product_tmpl_id.sudo().write({'uom_id': move.product_uom.id})
                                _logger.info(
                                    _("[组件扫码确认] 同时更新产品模板 %s 的计量单位: %s"),
                                    component_product.product_tmpl_id.name,
                                    move.product_uom.name
                                )
                            
                            # **关键**：强制刷新环境，确保父类方法能获取到最新值
                            # 使缓存失效（注意：invalidate_recordset 只能接受直接字段名，不能接受关联字段路径）
                            component_product.invalidate_recordset(['uom_id'])
                            if component_product.product_tmpl_id:
                                component_product.product_tmpl_id.invalidate_recordset(['uom_id'])
                            
                            # 重新从数据库加载产品记录，确保获取最新值
                            component_product = self.env['product.product'].browse(component_product.id)
                            
                            if old_uom_name == 'None':
                                _logger.info(
                                    _("[组件扫码确认] 为组件 %s 设置计量单位: %s (从移动行获取)"),
                                    component_product.name,
                                    move.product_uom.name
                                )
                            else:
                                _logger.warning(
                                    _("[组件扫码确认] 组件 %s 的计量单位类别不匹配（产品: %s, 移动行: %s），已更新为移动行的单位: %s"),
                                    component_product.name,
                                    old_uom_name,
                                    move.product_uom.name,
                                    move.product_uom.name
                                )
                            
                            # **关键**：确保 component_id 也使用更新后的产品
                            if self.component_id != component_product.id:
                                self.component_id = component_product.id
            
            # **关键**：在调用父类方法之前，确保所有可能被 register_consumed_materials 使用的产品都有正确的单位
            # register_consumed_materials 可能使用：
            # 1. self.component_id
            # 2. self.point_id.component_id
            # 3. self.selected_component_id
            
            # 检查并更新 component_id 指向的产品
            if self.component_id:
                component_from_id = self.component_id
                _logger.info(
                    _("[组件扫码确认] component_id 指向的产品: %s (ID=%s), 单位: %s"),
                    component_from_id.name,
                    component_from_id.id,
                    component_from_id.uom_id.name if component_from_id.uom_id else 'None'
                )
                # 如果 component_id 指向的产品单位与移动行不一致，也需要更新
                if self.production_id:
                    component_move_for_id = self.production_id.move_raw_ids.filtered(
                        lambda m: m.product_id.id == component_from_id.id
                    )
                    if component_move_for_id and component_move_for_id[0].product_uom:
                        move_for_id = component_move_for_id[0]
                        if not component_from_id.uom_id or (component_from_id.uom_id and component_from_id.uom_id.category_id != move_for_id.product_uom.category_id):
                            component_from_id.sudo().write({'uom_id': move_for_id.product_uom.id})
                            if component_from_id.product_tmpl_id:
                                component_from_id.product_tmpl_id.sudo().write({'uom_id': move_for_id.product_uom.id})
                            component_from_id.invalidate_recordset(['uom_id'])
                            if component_from_id.product_tmpl_id:
                                component_from_id.product_tmpl_id.invalidate_recordset(['uom_id'])
                            _logger.info(
                                _("[组件扫码确认] 已更新 component_id 指向的产品 %s 的计量单位: %s"),
                                component_from_id.name,
                                move_for_id.product_uom.name
                            )
            
            # 检查并更新 point_id.component_id 指向的产品（如果存在且不同）
            if self.point_id and self.point_id.component_id:
                point_component = self.point_id.component_id
                if not self.component_id or point_component.id != self.component_id.id:
                    _logger.info(
                        _("[组件扫码确认] point_id.component_id 指向的产品: %s (ID=%s), 单位: %s"),
                        point_component.name,
                        point_component.id,
                        point_component.uom_id.name if point_component.uom_id else 'None'
                    )
                    # 如果 point_id.component_id 指向的产品单位与移动行不一致，也需要更新
                    if self.production_id:
                        component_move_for_point = self.production_id.move_raw_ids.filtered(
                            lambda m: m.product_id.id == point_component.id
                        )
                        if component_move_for_point and component_move_for_point[0].product_uom:
                            move_for_point = component_move_for_point[0]
                            if not point_component.uom_id or (point_component.uom_id and point_component.uom_id.category_id != move_for_point.product_uom.category_id):
                                point_component.sudo().write({'uom_id': move_for_point.product_uom.id})
                                if point_component.product_tmpl_id:
                                    point_component.product_tmpl_id.sudo().write({'uom_id': move_for_point.product_uom.id})
                                point_component.invalidate_recordset(['uom_id'])
                                if point_component.product_tmpl_id:
                                    point_component.product_tmpl_id.invalidate_recordset(['uom_id'])
                                _logger.info(
                                    _("[组件扫码确认] 已更新 point_id.component_id 指向的产品 %s 的计量单位: %s"),
                                    point_component.name,
                                    move_for_point.product_uom.name
                                )
            
            # **关键修复**：在调用父类方法之前，强制刷新环境并重新加载所有相关记录
            # 确保 register_consumed_materials 能获取到最新的产品单位
            if self.component_id:
                # 强制刷新 component_id 的缓存（注意：invalidate_recordset 只能接受直接字段名，不能接受关联字段路径）
                self.component_id.invalidate_recordset(['uom_id'])
                # 同时刷新产品模板的缓存
                if self.component_id.product_tmpl_id:
                    self.component_id.product_tmpl_id.invalidate_recordset(['uom_id'])
                # 重新从数据库加载
                fresh_component = self.env['product.product'].browse(self.component_id.id)
                fresh_template = fresh_component.product_tmpl_id
                _logger.info(
                    _("[组件扫码确认] 刷新后 component_id 产品单位: %s, 产品模板单位: %s"),
                    fresh_component.uom_id.name if fresh_component.uom_id else 'None',
                    fresh_template.uom_id.name if fresh_template and fresh_template.uom_id else 'None'
                )
                # 如果产品模板没有单位，也设置一下
                if fresh_template and not fresh_template.uom_id and fresh_component.uom_id:
                    fresh_template.sudo().write({'uom_id': fresh_component.uom_id.id})
                    fresh_template.invalidate_recordset(['uom_id'])
                    _logger.info(
                        _("[组件扫码确认] 已为产品模板 %s 设置计量单位: %s"),
                        fresh_template.name,
                        fresh_component.uom_id.name
                    )
                # 确保 self.component_id 使用最新的记录
                self.component_id = fresh_component
            
            # 如果已经验证成功，记录日志
            _logger.info(
                _("[组件扫码确认] 质检通过: 质检ID=%s, 选择的组件=%s, 扫码的组件=%s, component_id=%s, 生产订单=%s"),
                self.id, 
                self.selected_component_id.name if self.selected_component_id else 'N/A',
                self.scanned_component_id.name if self.scanned_component_id else 'N/A',
                self.component_id.name if self.component_id else 'N/A',
                self.production_id.name if self.production_id else 'N/A'
            )
            
            # **关键**：在调用父类方法之前，确保环境已提交，使数据库更新生效
            # 但注意：在事务中，write 操作可能还没有提交
            # 所以我们需要确保在同一个事务中，register_consumed_materials 能读取到最新值
            # 通过 invalidate_recordset 和重新加载应该足够了
        
        # 调用父类方法执行质检通过
        # 注意：register_consumed_materials 可能在父类的 do_pass 中被调用
        res = super(QualityCheck, self).do_pass()
        
        return res
    
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
            
            # 记录日志
            self.message_post(
                body=_('组件验证失败<br/>选择的待登记组件：%s (%s)<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                    selected_product.name,
                    selected_product.default_code or '',
                    scanned_product.name,
                    scanned_product.default_code or '',
                    production.name
                )
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
        
        # 记录日志
        self.message_post(
            body=_('组件验证成功<br/>选择的待登记组件：%s (%s)<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                selected_product.name,
                selected_product.default_code or '',
                scanned_product.name,
                scanned_product.default_code or '',
                production.name
            )
        )
        
        _logger.info(
            _("[组件扫码确认] 验证成功: 质检ID=%s, 选择的组件=%s, 扫码的组件=%s, 生产订单=%s"),
            self.id, selected_product.name, scanned_product.name, production.name
        )
        
        # **关键修改**：验证成功后，自动通过质检并结束作业
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

