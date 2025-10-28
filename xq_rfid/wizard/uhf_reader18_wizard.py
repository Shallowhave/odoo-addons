# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class UHFReader18ConfigWizard(models.TransientModel):
    _name = 'uhf.reader18.config.wizard'
    _description = 'UHFReader18 配置向导'

    name = fields.Char('设备名称', default='UHFReader18-001', required=True)
    ip_address = fields.Char('设备IP地址', default='10.0.97.186', required=True)
    port = fields.Integer('设备端口', default=6000, required=True)
    device_address = fields.Integer('设备地址', default=0, help='RS485网络地址')
    timeout = fields.Integer('连接超时(秒)', default=5)
    
    # 测试结果
    connection_status = fields.Text('连接状态', readonly=True)
    inventory_result = fields.Text('询查结果', readonly=True)
    reader_info = fields.Text('读写器信息', readonly=True)

    def test_connection(self):
        """测试设备连接"""
        self.ensure_one()
        
        try:
            service = self.env['uhf.reader18.service']
            result = service.get_device_status(self.ip_address, self.port)
            
            if result['connected']:
                self.connection_status = f"✅ 连接成功！\n设备: {result['device_name']}\n模式: {result['mode']}\n{result['message']}"
                
                # 显示读写器信息
                if 'reader_info' in result and result['reader_info']['success']:
                    info = result['reader_info']
                    self.reader_info = f"""读写器信息：
版本: {info['version']}
类型: {info['reader_type']}
协议支持: {'6C' if info['protocol_support']['6c'] else ''} {'6B' if info['protocol_support']['6b'] else ''}
频率范围: {info['frequency_range']['min']}-{info['frequency_range']['max']}
功率: {info['power']}
询查时间: {info['scan_time']}"""
                else:
                    self.reader_info = "无法获取读写器详细信息"
            else:
                self.connection_status = f"❌ 连接失败！\n{result.get('error', '未知错误')}"
                self.reader_info = ""
                
        except Exception as e:
            self.connection_status = f"❌ 连接测试异常: {str(e)}"
            self.reader_info = ""
            _logger.error("连接测试异常: %s", e)
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def test_inventory(self):
        """测试询查标签"""
        self.ensure_one()
        
        try:
            service = self.env['uhf.reader18.service']
            result = service.inventory_tags(self.ip_address, self.port, self.device_address)
            
            if result['success']:
                epc_list = result.get('epc_list', [])
                if epc_list:
                    epc_text = '\n'.join([f"  - EPC: {epc['epc']} (长度: {epc['length']}字节)" for epc in epc_list])
                    self.inventory_result = f"✅ 询查成功！\n检测到 {result['num_tags']} 个标签:\n{epc_text}"
                else:
                    self.inventory_result = f"✅ 询查成功！\n检测到 {result['num_tags']} 个标签，但EPC数据为空。"
            else:
                self.inventory_result = f"❌ 询查失败！\n{result.get('error', '未知错误')}"
                
        except Exception as e:
            self.inventory_result = f"❌ 询查测试异常: {str(e)}"
            _logger.error("询查测试异常: %s", e)
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def save_config(self):
        """保存配置"""
        self.ensure_one()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('配置保存成功'),
                'message': _('UHFReader18设备配置已保存'),
                'type': 'success',
            }
        }

class UHFReader18DemoWizard(models.TransientModel):
    _name = 'uhf.reader18.demo.wizard'
    _description = 'UHFReader18 演示向导'

    device_name = fields.Char('设备名称', default='UHFReader18-001')
    ip_address = fields.Char('设备IP地址', default='10.0.97.186', required=True)
    port = fields.Integer('设备端口', default=6000, required=True)
    device_address = fields.Integer('设备地址', default=0)
    
    # 演示操作
    demo_operation = fields.Selection([
        ('inventory', '询查标签'),
        ('read_data', '读取标签数据'),
        ('write_data', '写入标签数据'),
        ('write_epc', '写入EPC号'),
        ('kill_tag', '销毁标签'),
        ('reader_info', '读取读写器信息'),
        ('set_power', '设置功率'),
        ('set_scan_time', '设置询查时间'),
    ], string='演示操作', default='inventory', required=True)
    
    # 读数据参数
    epc_hex = fields.Char('EPC (十六进制)', help='例如: E20000123456789012345678')
    mem_bank = fields.Selection([
        ('0x00', '保留区'),
        ('0x01', 'EPC存储区'),
        ('0x02', 'TID存储区'),
        ('0x03', 'User存储区'),
    ], string='存储区', default='0x03')
    word_ptr = fields.Integer('起始字地址', default=0, help='字地址 (1字=2字节)')
    num_words = fields.Integer('读取字数', default=4, help='字数')
    access_pwd = fields.Char('访问密码', default='00000000', help='8位十六进制密码')
    
    # 写数据参数
    write_data = fields.Text('写入数据', default='RFID001|PROD001|LOT001')
    write_words = fields.Text('写入字数据', help='十六进制字数据，用空格分隔，例如: 1234 5678 ABCD')
    
    # 写EPC参数
    new_epc = fields.Char('新EPC号', help='十六进制EPC号')
    
    # 销毁标签参数
    kill_pwd = fields.Char('销毁密码', default='00000000', help='8位十六进制密码')
    
    # 设置参数
    power_value = fields.Integer('功率值', default=20, help='功率范围: 0-30')
    scan_time_value = fields.Integer('询查时间', default=10, help='询查时间: 3-255 (对应300ms-25.5s)')
    
    # 结果显示
    demo_result = fields.Text('演示结果', readonly=True)

    def run_demo(self):
        """运行演示"""
        self.ensure_one()
        
        try:
            service = self.env['uhf.reader18.service']
            
            if self.demo_operation == 'inventory':
                result = service.inventory_tags(self.ip_address, self.port, self.device_address)
                
                if result['success']:
                    epc_list = result.get('epc_list', [])
                    if epc_list:
                        epc_text = '\n'.join([f"  - EPC: {epc['epc']} (长度: {epc['length']}字节)" for epc in epc_list])
                        self.demo_result = f"✅ 询查成功！\n检测到 {result['num_tags']} 个标签:\n{epc_text}"
                    else:
                        self.demo_result = f"✅ 询查成功！\n检测到 {result['num_tags']} 个标签，但EPC数据为空。"
                else:
                    self.demo_result = f"❌ 询查失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'read_data':
                if not self.epc_hex:
                    self.demo_result = "❌ 请先输入EPC十六进制字符串"
                else:
                    try:
                        pwd = int(self.access_pwd, 16)
                    except ValueError:
                        self.demo_result = "❌ 访问密码格式错误，请输入8位十六进制数"
                    else:
                        result = service.read_data(
                            self.ip_address, self.port, self.epc_hex,
                            int(self.mem_bank, 16), self.word_ptr, self.num_words, 
                            self.device_address, pwd
                        )
                        
                        if result['success']:
                            words_text = ' '.join([f"{word:04X}" for word in result['words']])
                            self.demo_result = f"✅ 读数据成功！\n读取到 {len(result['words'])} 个字:\n{words_text}\n原始数据: {result['data_hex']}"
                        else:
                            self.demo_result = f"❌ 读数据失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'write_data':
                if not self.epc_hex:
                    self.demo_result = "❌ 请先输入EPC十六进制字符串"
                elif not self.write_words:
                    self.demo_result = "❌ 请先输入要写入的字数据"
                else:
                    try:
                        pwd = int(self.access_pwd, 16)
                        words = [int(w, 16) for w in self.write_words.split()]
                    except ValueError:
                        self.demo_result = "❌ 字数据格式错误，请输入十六进制字数据"
                    else:
                        result = service.write_data(
                            self.ip_address, self.port, self.epc_hex,
                            int(self.mem_bank, 16), self.word_ptr, words,
                            self.device_address, pwd
                        )
                        
                        if result['success']:
                            self.demo_result = f"✅ 写数据成功！\n已写入 {len(words)} 个字到存储区 {self.mem_bank}"
                        else:
                            self.demo_result = f"❌ 写数据失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'write_epc':
                if not self.new_epc:
                    self.demo_result = "❌ 请先输入新EPC号"
                else:
                    try:
                        pwd = int(self.access_pwd, 16)
                    except ValueError:
                        self.demo_result = "❌ 访问密码格式错误，请输入8位十六进制数"
                    else:
                        result = service.write_epc(
                            self.ip_address, self.port, self.new_epc,
                            self.device_address, pwd
                        )
                        
                        if result['success']:
                            self.demo_result = f"✅ 写EPC成功！\n新EPC: {self.new_epc}"
                        else:
                            self.demo_result = f"❌ 写EPC失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'kill_tag':
                if not self.epc_hex:
                    self.demo_result = "❌ 请先输入EPC十六进制字符串"
                else:
                    try:
                        kill_pwd = int(self.kill_pwd, 16)
                    except ValueError:
                        self.demo_result = "❌ 销毁密码格式错误，请输入8位十六进制数"
                    else:
                        result = service.kill_tag(
                            self.ip_address, self.port, self.epc_hex,
                            kill_pwd, self.device_address
                        )
                        
                        if result['success']:
                            self.demo_result = f"✅ 销毁标签成功！\nEPC: {self.epc_hex}"
                        else:
                            self.demo_result = f"❌ 销毁标签失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'reader_info':
                result = service.get_reader_info(self.ip_address, self.port, self.device_address)
                
                if result['success']:
                    info = result
                    self.demo_result = f"""✅ 读取读写器信息成功！
版本: {info['version']}
类型: {info['reader_type']}
协议支持: {'6C' if info['protocol_support']['6c'] else ''} {'6B' if info['protocol_support']['6b'] else ''}
频率范围: {info['frequency_range']['min']}-{info['frequency_range']['max']}
功率: {info['power']}
询查时间: {info['scan_time']}"""
                else:
                    self.demo_result = f"❌ 读取读写器信息失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'set_power':
                result = service.set_power(
                    self.ip_address, self.port, self.power_value, self.device_address
                )
                
                if result['success']:
                    self.demo_result = f"✅ 设置功率成功！\n功率值: {self.power_value}"
                else:
                    self.demo_result = f"❌ 设置功率失败！\n{result.get('error', '未知错误')}"
                    
            elif self.demo_operation == 'set_scan_time':
                result = service.set_scan_time(
                    self.ip_address, self.port, self.scan_time_value, self.device_address
                )
                
                if result['success']:
                    self.demo_result = f"✅ 设置询查时间成功！\n询查时间: {self.scan_time_value} (对应 {self.scan_time_value * 100}ms)"
                else:
                    self.demo_result = f"❌ 设置询查时间失败！\n{result.get('error', '未知错误')}"
                    
        except Exception as e:
            self.demo_result = f"❌ 演示异常: {str(e)}"
            _logger.error("演示异常: %s", e)
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
