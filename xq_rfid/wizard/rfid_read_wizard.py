# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
import logging

_logger = logging.getLogger(__name__)


class RfidReadWizard(models.TransientModel):
    _name = 'rfid.read.wizard'
    _description = 'RFID 读取向导'

    device_id = fields.Many2one(
        'rfid.device.config',
        string='RFID 设备',
        required=True,
        domain="[('device_type', '=', 'uhf_reader18'), ('active', '=', True)]"
    )
    
    epc_hex = fields.Char(
        string='EPC 标签',
        help='要读取的RFID标签EPC（十六进制）',
        default='1100EE00E28068940000502C6FE618BB93DF'
    )
    
    mem_bank = fields.Selection([
        ('0x01', 'EPC 存储区'),
        ('0x02', 'TID 存储区'),
        ('0x03', '用户存储区'),
        ('0x00', '保留存储区')
    ], string='存储区', default='0x03', required=True)
    
    word_ptr = fields.Integer(
        string='起始地址',
        default=0,
        help='读取数据的起始字地址'
    )
    
    word_count = fields.Integer(
        string='字数',
        default=20,
        help='要读取的字数'
    )
    
    read_result = fields.Text(
        string='读取结果',
        readonly=True
    )
    
    parsed_data = fields.Text(
        string='解析数据',
        readonly=True
    )
    
    read_status = fields.Selection([
        ('pending', '待读取'),
        ('reading', '读取中'),
        ('success', '读取成功'),
        ('failed', '读取失败')
    ], string='读取状态', default='pending', readonly=True)
    
    read_time = fields.Datetime(
        string='读取时间',
        readonly=True
    )

    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super(RfidReadWizard, self).default_get(fields_list)
        
        # 默认选择第一个可用的UHFReader18设备
        default_device = self.env['rfid.device.config'].search([
            ('device_type', '=', 'uhf_reader18'),
            ('active', '=', True)
        ], limit=1)
        
        if default_device:
            res['device_id'] = default_device.id
            
        return res

    def action_read_rfid(self):
        """执行RFID读取操作"""
        self.ensure_one()
        
        if not self.device_id:
            raise UserError(_('请选择RFID设备！'))
        
        if not self.epc_hex:
            raise UserError(_('请输入EPC标签！'))
        
        # 检查设备连接状态
        if self.device_id.connection_status != 'connected':
            raise UserError(_('RFID设备未连接，请先测试连接！'))
        
        try:
            # 更新状态
            self.write({
                'read_status': 'reading',
                'read_time': fields.Datetime.now()
            })
            
            # 获取UHFReader18服务
            uhf_service = self.env['uhf.reader18.service']
            
            # 转换存储区参数
            mem_bank = int(self.mem_bank, 16)
            
            # 0. 检查并设置设备工作模式
            work_mode_result = uhf_service.get_work_mode(
                ip=self.device_id.ip_address,
                port=int(self.device_id.port)
            )
            
            if work_mode_result.get('success') and work_mode_result.get('is_active_mode'):
                # 设备处于主动模式，需要切换到应答模式
                set_mode_result = uhf_service.set_work_mode(
                    ip=self.device_id.ip_address,
                    port=int(self.device_id.port)
                )
                if not set_mode_result.get('success'):
                    raise UserError(_('无法设置设备为应答模式：%s') % set_mode_result.get('error'))
            
            # 执行读取操作
            result = uhf_service.read_data(
                ip=self.device_id.ip_address,
                port=int(self.device_id.port),
                epc_hex=self.epc_hex,
                mem_bank=mem_bank,
                word_ptr=self.word_ptr,
                num_words=self.word_count
            )
            
            if result.get('success'):
                # 读取成功
                raw_data = result.get('data', [])
                self.write({
                    'read_result': str(raw_data),
                    'read_status': 'success',
                    'parsed_data': self._parse_read_data(raw_data)
                })
                
                # 更新设备读取计数器
                self.device_id.read_count += 1
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('读取成功'),
                        'message': _('RFID标签数据读取成功！'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                # 读取失败
                error_msg = result.get('error', '未知错误')
                self.write({
                    'read_result': f'读取失败: {error_msg}',
                    'read_status': 'failed'
                })
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('读取失败'),
                        'message': _('RFID标签数据读取失败：%s') % error_msg,
                        'type': 'warning',
                        'sticky': True,
                    }
                }
                
        except Exception as e:
            _logger.error("RFID读取异常: %s", str(e))
            self.write({
                'read_result': f'读取异常: {str(e)}',
                'read_status': 'failed'
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('读取异常'),
                    'message': _('RFID读取过程中发生异常：%s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def _parse_read_data(self, raw_data):
        """解析读取的原始数据"""
        try:
            if not raw_data:
                return "无数据"
            
            # 将字数据转换为字节
            byte_data = b''
            for word in raw_data:
                byte_data += bytes([(word >> 8) & 0xFF, word & 0xFF])
            
            # 尝试解码为UTF-8字符串 - 简化版本，只显示产品序列号
            try:
                decoded_text = byte_data.decode('utf-8').rstrip('\x00').strip()
                
                # 简化显示，只显示产品序列号
                if decoded_text:
                    return f"产品序列号: {decoded_text}"
                else:
                    return "无产品序列号数据"
                    
            except UnicodeDecodeError:
                # 无法解码为UTF-8，返回十六进制
                return f"十六进制数据: {byte_data.hex().upper()}"
                
        except Exception as e:
            return f"解析失败: {str(e)}"

    def action_test_connection(self):
        """测试设备连接"""
        self.ensure_one()
        
        if not self.device_id:
            raise UserError(_('请选择RFID设备！'))
        
        try:
            # 获取UHFReader18服务
            uhf_service = self.env['uhf.reader18.service']
            
            # 执行连接测试
            result = uhf_service.test_connection(
                ip=self.device_id.ip_address,
                port=int(self.device_id.port)
            )
            
            if result.get('success'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('连接成功'),
                        'message': _('设备连接正常！'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('连接失败'),
                        'message': result.get('error', '连接失败'),
                        'type': 'warning',
                        'sticky': True,
                    }
                }
                
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('连接异常'),
                    'message': _('连接测试异常：%s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }
