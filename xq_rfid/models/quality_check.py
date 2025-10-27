# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import UserError


class QualityCheck(models.Model):
    _inherit = 'quality.check'
    
    # RFID 标签关联
    rfid_tag_id = fields.Many2one(
        'rfid.tag', 
        string='RFID 标签',
        readonly=True,
        help='质检时生成的 RFID 标签'
    )
    
    rfid_tag_name = fields.Char(
        related='rfid_tag_id.name',
        string='RFID 编号',
        readonly=True
    )
    
    test_type = fields.Char(
        string='测试类型',
        related='point_id.test_type_id.technical_name',
        readonly=True,
        help='质检点的技术名称，用于判断是否为 RFID 测试'
    )
    
    def do_pass(self):
        """
        质检通过时自动生成 RFID 标签
        """
        # 如果是 RFID 标签类型的质检，先生成 RFID，再通过质检
        if self.test_type == 'rfid_label' and self.production_id and not self.rfid_tag_id:
            # 使用生产订单的成品批次号
            lot = self.production_id.lot_producing_id
            
            if lot:
                try:
                    # 调用生产订单的生成方法
                    rfid_tag = self.production_id.generate_rfid_for_lot(
                        lot_id=lot,
                        quality_check_id=self.id
                    )
                    
                    # 关联到当前质检
                    self.rfid_tag_id = rfid_tag.id
                    
                    # 如果质检点配置了需要 RFID 设备，则调用写入接口
                    if self.point_id.rfid_device_required:
                        try:
                            self._write_to_rfid_device(rfid_tag)
                        except Exception as e:
                            # 设备写入失败不影响 RFID 生成，只记录日志
                            import logging
                            _logger = logging.getLogger(__name__)
                            _logger.warning('RFID 设备写入失败：%s', str(e))
                    
                except Exception as e:
                    # RFID 生成失败会抛出异常，阻止质检通过
                    raise UserError(_('RFID 生成失败：%s') % str(e))
        
        # 调用父类方法执行质检通过
        res = super(QualityCheck, self).do_pass()
        
        return res
    
    def action_view_rfid_tag(self):
        """查看关联的 RFID 标签"""
        self.ensure_one()
        
        if not self.rfid_tag_id:
            raise UserError(_('该质检点未生成 RFID 标签！'))
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('RFID 标签'),
            'res_model': 'rfid.tag',
            'res_id': self.rfid_tag_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def _write_to_rfid_device(self, rfid_tag):
        """
        写入 RFID 设备（硬件接口）
        
        此方法为硬件对接预留接口，子类或其他模块可以重写此方法
        实现具体的硬件写入逻辑
        
        :param rfid_tag: RFID 标签记录
        :return: True 表示写入成功，False 或抛出异常表示失败
        """
        # 调用 RFID 设备服务
        device_service = self.env['rfid.device.service']
        
        # 准备要写入的数据
        write_data = {
            'rfid_number': rfid_tag.name,
            'product_code': self.product_id.default_code or '',
            'product_name': self.product_id.name,
            'lot_number': self.lot_id.name,
            'production_date': rfid_tag.production_date,
            'production_order': self.production_id.name,
        }
        
        # 调用设备写入方法
        result = device_service.write_rfid_tag(write_data)
        
        if not result.get('success'):
            raise UserError(_('RFID 设备写入失败：%s') % result.get('error', '未知错误'))
        
        # 记录写入日志
        rfid_tag.message_post(
            body=_('RFID 标签已写入硬件设备<br/>质检点: %s<br/>设备响应: %s') % (
                self.point_id.title,
                result.get('message', '成功')
            )
        )
        
        return True

