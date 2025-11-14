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
            
            # 如果已经验证成功，记录日志
            _logger.info(
                _("[组件扫码确认] 质检通过: 质检ID=%s, 选择的组件=%s, 扫码的组件=%s, 生产订单=%s"),
                self.id, 
                self.selected_component_id.name if self.selected_component_id else 'N/A',
                self.scanned_component_id.name if self.scanned_component_id else 'N/A',
                self.production_id.name if self.production_id else 'N/A'
            )
        
        # 调用父类方法执行质检通过
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
        
        return {'success': True, 'message': self.component_verification_message}
    
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

