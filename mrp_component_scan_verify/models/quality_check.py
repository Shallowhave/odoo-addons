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
    
    def do_pass(self):
        """
        质检通过时执行组件扫码确认
        """
        # 如果是组件扫码确认类型的质检，执行验证
        if self.test_type == 'component_scan_verify':
            # 检查是否已经验证过
            if not self.scanned_component_id:
                raise UserError(_('请先扫码确认组件！'))
            
            # 检查验证结果
            if self.component_verification_result != 'matched':
                raise UserError(_('组件验证失败，无法通过质检！\n%s') % (
                    self.component_verification_message or _('请确保扫码的组件匹配生产订单的BOM')
                ))
            
            # 如果已经验证成功，记录日志
            _logger.info(
                _("[组件扫码确认] 质检通过: 质检ID=%s, 组件=%s, 生产订单=%s"),
                self.id, 
                self.scanned_component_id.name if self.scanned_component_id else 'N/A',
                self.production_id.name if self.production_id else 'N/A'
            )
        
        # 调用父类方法执行质检通过
        res = super(QualityCheck, self).do_pass()
        
        return res
    
    def _verify_component(self):
        """
        验证扫码的组件是否匹配生产订单的BOM
        
        此方法由前端调用，用于实时验证
        验证逻辑：
        1. 获取生产订单的组件列表（从 move_raw_ids）
        2. 验证扫码的组件是否在组件列表中
        3. 记录验证结果
        """
        if not self.production_id:
            raise UserError(_('无法获取生产订单信息，请确保质检点关联了生产订单！'))
        
        if not self.scanned_component_id:
            raise UserError(_('请先扫码确认组件！'))
        
        production = self.production_id
        
        # 获取生产订单需要的组件列表
        required_components = production.move_raw_ids.mapped('product_id')
        
        if not required_components:
            raise UserError(_('生产订单 %s 没有配置组件，请检查BOM！') % production.name)
        
        # 验证扫码的组件是否在组件列表中
        scanned_product = self.scanned_component_id
        
        if scanned_product not in required_components:
            # 组件不匹配
            self.component_verification_result = 'mismatched'
            self.component_verification_message = _(
                '组件不匹配！\n'
                '扫码的组件：%s (%s)\n'
                '生产订单需要的组件：%s'
            ) % (
                scanned_product.name,
                scanned_product.default_code or '',
                ', '.join(required_components.mapped('name'))
            )
            
            # 记录日志
            self.message_post(
                body=_('组件验证失败<br/>扫码的组件：%s (%s)<br/>生产订单：%s<br/>需要的组件：%s') % (
                    scanned_product.name,
                    scanned_product.default_code or '',
                    production.name,
                    ', '.join(required_components.mapped('name'))
                )
            )
            
            return {'success': False, 'message': self.component_verification_message}
        
        # 组件匹配
        self.component_verification_result = 'matched'
        self.component_verification_message = _(
            '组件验证成功！\n'
            '扫码的组件：%s (%s)\n'
            '匹配生产订单：%s'
        ) % (
            scanned_product.name,
            scanned_product.default_code or '',
            production.name
        )
        
        # 记录日志
        self.message_post(
            body=_('组件验证成功<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                scanned_product.name,
                scanned_product.default_code or '',
                production.name
            )
        )
        
        _logger.info(
            _("[组件扫码确认] 验证成功: 质检ID=%s, 组件=%s, 生产订单=%s"),
            self.id, scanned_product.name, production.name
        )
        
        return {'success': True, 'message': _('组件验证成功')}
    
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

