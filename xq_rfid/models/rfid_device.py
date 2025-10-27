# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
# RFID 硬件设备接口
# 为 RFID 读写器预留的抽象接口层
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class RfidDeviceService(models.AbstractModel):
    """
    RFID 设备服务抽象模型
    
    此模型提供 RFID 硬件设备的统一接口，具体的硬件实现应该
    继承此模型并实现具体的读写方法。
    
    使用示例：
    --------
    # 在其他模块中继承并实现具体设备驱动
    class RfidDeviceServiceImpl(models.Model):
        _inherit = 'rfid.device.service'
        
        def _connect_device(self):
            # 实现具体的设备连接逻辑
            pass
    """
    
    _name = 'rfid.device.service'
    _description = 'RFID 设备服务接口'
    
    def write_rfid_tag(self, data):
        """
        写入 RFID 标签（抽象方法）
        
        :param data: 要写入的数据字典，包含：
            - rfid_number: RFID 编号
            - product_code: 产品编码
            - product_name: 产品名称
            - lot_number: 批次号
            - production_date: 生产日期
            - production_order: 生产订单号
            - 其他自定义数据...
            
        :return: 字典格式的结果
            {
                'success': True/False,
                'message': '写入成功/失败信息',
                'error': '错误详情（可选）',
                'device_response': '设备原始响应（可选）'
            }
        """
        # 默认实现：模拟模式（无实际硬件）
        _logger.info('RFID 写入（模拟模式）: %s', data)
        
        return {
            'success': True,
            'message': '模拟写入成功（未连接实际设备）',
            'data': data
        }
    
    def read_rfid_tag(self):
        """
        读取 RFID 标签（抽象方法）
        
        :return: 字典格式的结果
            {
                'success': True/False,
                'rfid_number': 'RFID编号',
                'data': {...},  # 读取到的其他数据
                'error': '错误信息（可选）'
            }
        """
        _logger.info('RFID 读取（模拟模式）')
        
        return {
            'success': False,
            'error': '模拟模式：未连接实际设备'
        }
    
    def verify_rfid_tag(self, rfid_number):
        """
        验证 RFID 标签（抽象方法）
        
        :param rfid_number: RFID 编号
        :return: 字典格式的结果
            {
                'success': True/False,
                'valid': True/False,
                'data': {...},  # 标签数据
                'error': '错误信息（可选）'
            }
        """
        _logger.info('RFID 验证（模拟模式）: %s', rfid_number)
        
        # 在数据库中查找
        rfid_tag = self.env['rfid.tag'].search([
            ('name', '=', rfid_number)
        ], limit=1)
        
        if rfid_tag:
            return {
                'success': True,
                'valid': True,
                'data': {
                    'rfid_number': rfid_tag.name,
                    'product': rfid_tag.product_id.name,
                    'lot': rfid_tag.stock_prod_lot_id.name,
                    'production_order': rfid_tag.production_id.name if rfid_tag.production_id else '',
                }
            }
        else:
            return {
                'success': True,
                'valid': False,
                'error': 'RFID 标签不存在'
            }
    
    def erase_rfid_tag(self):
        """
        擦除 RFID 标签（抽象方法）
        
        :return: 字典格式的结果
        """
        _logger.info('RFID 擦除（模拟模式）')
        
        return {
            'success': False,
            'error': '模拟模式：未连接实际设备'
        }
    
    def get_device_status(self):
        """
        获取设备状态（抽象方法）
        
        :return: 字典格式的结果
            {
                'connected': True/False,
                'device_name': '设备名称',
                'firmware_version': '固件版本',
                'error': '错误信息（可选）'
            }
        """
        return {
            'connected': False,
            'device_name': '模拟设备',
            'firmware_version': '1.0.0-mock',
            'mode': 'simulation'
        }


class RfidDeviceConfig(models.Model):
    """
    RFID 设备配置模型
    
    用于存储 RFID 读写器的配置信息
    """
    
    _name = 'rfid.device.config'
    _description = 'RFID 设备配置'
    _order = 'sequence, id'
    
    name = fields.Char(string='设备名称', required=True)
    sequence = fields.Integer(string='序号', default=10)
    active = fields.Boolean(string='启用', default=True)
    
    device_type = fields.Selection([
        ('simulation', '模拟设备'),
        ('usb', 'USB 读写器'),
        ('serial', '串口读写器'),
        ('network', '网络读写器'),
        ('custom', '自定义设备'),
    ], string='设备类型', default='simulation', required=True)
    
    # 连接参数
    connection_string = fields.Char(
        string='连接字符串',
        help='设备连接参数，如：COM3、192.168.1.100:8080 等'
    )
    
    port = fields.Char(string='端口')
    baudrate = fields.Integer(string='波特率', default=9600)
    timeout = fields.Integer(string='超时时间（秒）', default=5)
    
    # 高级配置
    auto_connect = fields.Boolean(
        string='自动连接',
        default=True,
        help='系统启动时自动连接设备'
    )
    
    retry_times = fields.Integer(
        string='重试次数',
        default=3,
        help='操作失败时的重试次数'
    )
    
    # 状态信息
    last_connected = fields.Datetime(string='最后连接时间', readonly=True)
    connection_status = fields.Selection([
        ('disconnected', '未连接'),
        ('connected', '已连接'),
        ('error', '连接错误'),
    ], string='连接状态', default='disconnected', readonly=True)
    
    error_message = fields.Text(string='错误信息', readonly=True)
    
    # 统计信息
    write_count = fields.Integer(string='写入次数', default=0, readonly=True)
    read_count = fields.Integer(string='读取次数', default=0, readonly=True)
    
    notes = fields.Text(string='备注')
    
    def action_test_connection(self):
        """测试设备连接"""
        self.ensure_one()
        
        device_service = self.env['rfid.device.service']
        status = device_service.get_device_status()
        
        if status.get('connected'):
            self.connection_status = 'connected'
            self.last_connected = fields.Datetime.now()
            self.error_message = False
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('连接成功'),
                    'message': _('设备 %s 连接正常') % status.get('device_name', ''),
                    'type': 'success',
                }
            }
        else:
            self.connection_status = 'error'
            self.error_message = status.get('error', '未知错误')
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('连接失败'),
                    'message': status.get('error', '无法连接设备'),
                    'type': 'warning',
                    'sticky': True,
                }
            }
    
    def action_write_test_tag(self):
        """写入测试标签"""
        self.ensure_one()
        
        device_service = self.env['rfid.device.service']
        
        test_data = {
            'rfid_number': 'TEST-001',
            'product_code': 'TEST',
            'product_name': '测试产品',
            'lot_number': 'TEST-LOT-001',
            'production_date': fields.Datetime.now(),
        }
        
        result = device_service.write_rfid_tag(test_data)
        
        if result.get('success'):
            self.write_count += 1
            message = result.get('message', '写入成功')
            msg_type = 'success'
        else:
            message = result.get('error', '写入失败')
            msg_type = 'warning'
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('测试写入'),
                'message': message,
                'type': msg_type,
            }
        }
    
    def action_read_test_tag(self):
        """读取测试标签"""
        self.ensure_one()
        
        device_service = self.env['rfid.device.service']
        result = device_service.read_rfid_tag()
        
        if result.get('success'):
            self.read_count += 1
            message = _('读取成功: %s') % result.get('rfid_number', '')
            msg_type = 'success'
        else:
            message = result.get('error', '读取失败')
            msg_type = 'warning'
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('测试读取'),
                'message': message,
                'type': msg_type,
            }
        }
    
    def action_view_write_logs(self):
        """查看写入日志"""
        self.ensure_one()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('写入统计'),
                'message': _('设备 %s 累计写入次数: %d') % (self.name, self.write_count),
                'type': 'info',
            }
        }
    
    def action_view_read_logs(self):
        """查看读取日志"""
        self.ensure_one()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('读取统计'),
                'message': _('设备 %s 累计读取次数: %d') % (self.name, self.read_count),
                'type': 'info',
            }
        }

