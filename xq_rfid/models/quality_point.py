# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api


class QualityPoint(models.Model):
    _inherit = 'quality.point'
    
    # RFID 设备配置（可选）
    rfid_device_required = fields.Boolean(
        string='需要 RFID 设备',
        help='启用后，生成 RFID 时将调用硬件设备接口进行写入操作'
    )
    
    # RFID 设备选择
    rfid_device_id = fields.Many2one(
        'rfid.device.config',
        string='RFID 设备',
        domain="[('device_type', '=', 'uhf_reader18'), ('active', '=', True)]",
        help='选择用于 RFID 写入的设备'
    )
    
    @api.model
    def create(self, vals):
        """创建质量控制点时，如果是RFID标签写入类型，默认选择第一个可用设备"""
        record = super(QualityPoint, self).create(vals)
        
        # 如果是RFID标签写入类型且没有选择设备，则默认选择第一个可用设备
        if (record.test_type_id and 
            record.test_type_id.technical_name == 'rfid_write' and 
            not record.rfid_device_id):
            
            # 查找第一个可用的UHFReader18设备
            default_device = self.env['rfid.device.config'].search([
                ('device_type', '=', 'uhf_reader18'),
                ('active', '=', True)
            ], limit=1)
            
            if default_device:
                record.rfid_device_id = default_device.id
                
        return record
    
    @api.onchange('test_type_id')
    def _onchange_test_type_id(self):
        """当测试类型改变时，自动设置RFID设备"""
        if (self.test_type_id and 
            self.test_type_id.technical_name == 'rfid_write' and 
            not self.rfid_device_id):
            
            # 查找第一个可用的UHFReader18设备
            default_device = self.env['rfid.device.config'].search([
                ('device_type', '=', 'uhf_reader18'),
                ('active', '=', True)
            ], limit=1)
            
            if default_device:
                self.rfid_device_id = default_device.id

