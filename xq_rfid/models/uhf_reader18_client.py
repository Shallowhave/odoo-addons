# -*- coding: utf-8 -*-

import socket
import struct
import logging
import time
from odoo import fields, models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class UHFReader18Service(models.AbstractModel):
    _name = 'uhf.reader18.service'
    _description = 'UHFReader18 TCP/IP 服务接口'
    _inherit = 'rfid.device.service'
    def _crc16(self, data):
        """计算CRC16校验码 - 按照用户手册算法"""
        PRESET_VALUE = 0xFFFF
        POLYNOMIAL = 0x8408
        
        uiCrcValue = PRESET_VALUE
        for ucI in range(len(data)):
            uiCrcValue = uiCrcValue ^ data[ucI]
            for ucJ in range(8):
                if uiCrcValue & 0x0001:
                    uiCrcValue = (uiCrcValue >> 1) ^ POLYNOMIAL
                else:
                    uiCrcValue = uiCrcValue >> 1
        
        return uiCrcValue & 0xFFFF

    def _build_frame(self, address, command, data_bytes=b''):
        """构建通信帧 - 按照用户手册格式"""
        # Len(1字节) + Adr(1字节) + Cmd(1字节) + Data(N字节) + CRC16(2字节)
        frame_data = struct.pack('<BB', address, command) + data_bytes
        frame_len = len(frame_data) + 2  # +2 for CRC, Len字段不包含Len字节本身
        
        # CRC计算范围：从Len到Data（不含CRC自身）
        crc_data = struct.pack('<B', frame_len) + frame_data
        crc = self._crc16(crc_data)
        
        # CRC低字节在前，高字节在后
        crc_bytes = struct.pack('<H', crc)
        
        full_frame = struct.pack('<B', frame_len) + frame_data + crc_bytes
        return full_frame

    def _send_command(self, ip, port, frame, timeout=5):
        """发送命令到设备"""
        try:
            _logger.info("开始TCP通信: %s:%s", ip, port)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                _logger.info("正在连接TCP...")
                sock.connect((ip, port))
                _logger.info("TCP连接成功")
                
                _logger.info("发送命令帧: %s", frame.hex().upper())
                sock.sendall(frame)
                _logger.info("命令发送成功")
                
                # 等待一小段时间让设备处理
                _logger.info("等待设备处理...")
                time.sleep(0.5)
                
                # 尝试接收所有可用数据
                full_response = b''
                attempts = 0
                max_attempts = 50  # 5秒
                
                _logger.info("开始接收数据...")
                while attempts < max_attempts:
                    try:
                        # 设置较短的超时时间
                        sock.settimeout(0.1)
                        data = sock.recv(1024)
                        if data:
                            full_response += data
                            _logger.info("收到数据: %s", data.hex().upper())
                            # 如果已经收到足够的数据，停止接收
                            if len(full_response) >= 5:  # 至少要有 Len + Adr + reCmd + Status + CRC
                                break
                        else:
                            _logger.info("第%d次尝试: 连接关闭", attempts + 1)
                            break
                    except socket.timeout:
                        _logger.info("第%d次尝试: 超时", attempts + 1)
                    except Exception as e:
                        _logger.info("第%d次尝试: 异常 %s", attempts + 1, e)
                        break
                    
                    attempts += 1
                    time.sleep(0.1)
                
                _logger.info("接收尝试完成，共尝试%d次", attempts)
                
                if not full_response:
                    _logger.error("未收到任何设备响应")
                    raise UserError(_("未收到设备响应"))
                
                _logger.info("收到完整响应: %s", full_response.hex().upper())
                
                # 基本长度检查
                if len(full_response) < 5:
                    raise UserError(_("响应数据过短"))
                
                # 解析响应长度
                response_len = full_response[0]
                expected_total_len = response_len + 1  # +1 for Len byte itself
                
                if len(full_response) != expected_total_len:
                    _logger.warning("响应长度不匹配。预期: %d, 实际: %d", expected_total_len, len(full_response))
                
                # CRC校验
                if len(full_response) >= 3:
                    received_crc = struct.unpack('<H', full_response[-2:])[0]
                    calculated_crc = self._crc16(full_response[:-2])
                    
                    if received_crc != calculated_crc:
                        _logger.warning("CRC校验失败！接收: %04X, 计算: %04X", received_crc, calculated_crc)
                
                return full_response
                
        except socket.error as e:
            _logger.error("TCP通信错误: %s", e)
            raise UserError(_("TCP通信错误: %s") % e)
        except Exception as e:
            _logger.error("发送命令时发生错误: %s", e)
            raise UserError(_("发送命令时发生错误: %s") % e)

    def _parse_response(self, response_frame):
        """解析响应帧"""
        if len(response_frame) < 6:  # 最小长度: Len+Adr+reCmd+Status+CRC
            return {'success': False, 'error': _("响应帧过短")}
        
        # 跳过 Len 字节
        addr = response_frame[1]
        re_cmd = response_frame[2]
        status = response_frame[3]
        data = response_frame[4:-2]  # 排除 Len, Adr, reCmd, Status, CRC
        
        return {
            'success': status == 0x00,
            'address': addr,
            'command': re_cmd,
            'status': status,
            'data': data,
            'status_text': self._get_status_text(status)
        }

    def _get_status_text(self, status):
        """获取状态码文本描述"""
        status_map = {
            0x00: "操作成功",
            0x01: "询查时间结束前返回",
            0x02: "指定的询查时间溢出",
            0x03: "本条消息之后，还有消息",
            0x04: "读写器存储空间已满",
            0x05: "访问密码错误",
            0x09: "销毁标签失败",
            0x0a: "销毁密码不能为全0",
            0x0b: "电子标签不支持该命令",
            0x0c: "对该命令访问密码不能为全0",
            0x0d: "电子标签已经被设置了读保护，不能再次设置",
            0x0e: "电子标签没有被设置读保护，不需要解锁",
            0x10: "有字节空间被锁定，写入失败",
            0x11: "不能锁定",
            0x12: "已经锁定，不能再次锁定",
            0x13: "参数保存失败，但设置的值在读写器断电前有效",
            0x14: "无法调整",
            0x15: "询查时间结束前返回(6B)",
            0x16: "指定的询查时间溢出(6B)",
            0x17: "本条消息之后，还有消息(6B)",
            0x18: "读写器存储空间已满(6B)",
            0x19: "电子标签不支持该命令或者访问密码不能为0",
            0xEE: "电子标签不支持该命令",
            0xF9: "命令执行出错",
            0xFA: "有电子标签，但通信不畅，操作失败",
            0xFB: "无电子标签可操作",
            0xFC: "电子标签返回错误代码",
            0xFD: "命令长度错误",
            0xFE: "不合法的命令",
            0xFF: "参数错误"
        }
        return status_map.get(status, f"未知状态码: {status:02X}")

    # ==================== EPC C1G2 命令 ====================
    
    @api.model
    def inventory_tags(self, ip, port, address=0x00, tid_addr=None, tid_len=None):
        """
        询查标签 (0x01)
        :param tid_addr: TID区起始字地址，None表示询查EPC
        :param tid_len: TID区数据字数，None表示询查EPC
        """
        data_bytes = b''
        if tid_addr is not None and tid_len is not None:
            data_bytes = struct.pack('<BB', tid_addr, tid_len)
        
        command_frame = self._build_frame(address, 0x01, data_bytes)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        if result['success']:
            return self._parse_inventory_response(result['data'])
        else:
            return {'success': False, 'error': result['status_text']}

    def _parse_inventory_response(self, data):
        """解析询查标签响应"""
        if len(data) < 1:
            return {'success': False, 'error': _("响应数据为空")}
        
        num_tags = data[0]
        epc_data = data[1:]
        
        epc_list = []
        pos = 0
        
        for i in range(num_tags):
            if pos >= len(epc_data):
                break
            
            # EPC长度（字节）
            epc_len = epc_data[pos]
            pos += 1
            
            if pos + epc_len > len(epc_data):
                break
            
            # EPC数据
            epc_bytes = epc_data[pos:pos + epc_len]
            epc_hex = epc_bytes.hex().upper()
            epc_list.append({
                'epc': epc_hex,
                'length': epc_len,
                'words': epc_len // 2
            })
            
            pos += epc_len
        
        return {
            'success': True,
            'num_tags': num_tags,
            'epc_list': epc_list
        }

    @api.model
    def read_data(self, ip, port, epc_hex, mem_bank, word_ptr, num_words, 
                  address=0x00, pwd=0x00000000, mask_addr=None, mask_len=None):
        """
        读数据 (0x02)
        :param epc_hex: EPC十六进制字符串
        :param mem_bank: 存储区 (0x00:保留区, 0x01:EPC区, 0x02:TID区, 0x03:用户区)
        :param word_ptr: 起始字地址
        :param num_words: 读取字数
        :param pwd: 访问密码
        :param mask_addr: 掩模起始字节地址
        :param mask_len: 掩模字节数
        """
        try:
            epc_bytes = bytes.fromhex(epc_hex)
        except ValueError:
            raise UserError(_("无效的EPC十六进制字符串"))
        
        # 构建数据字段
        enum = len(epc_bytes) // 2  # EPC长度（字）
        data_field = struct.pack('<B', enum) + epc_bytes
        data_field += struct.pack('<BBB', mem_bank, word_ptr, num_words)
        data_field += struct.pack('<I', pwd)  # 4字节密码
        
        if mask_addr is not None and mask_len is not None:
            data_field += struct.pack('<BB', mask_addr, mask_len)
        
        command_frame = self._build_frame(address, 0x02, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        if result['success']:
            return self._parse_read_data_response(result['data'])
        else:
            return {'success': False, 'error': result['status_text']}

    def _parse_read_data_response(self, data):
        """解析读数据响应"""
        if len(data) % 2 != 0:
            return {'success': False, 'error': _("数据长度不是偶数")}
        
        words = []
        for i in range(0, len(data), 2):
            word = struct.unpack('>H', data[i:i+2])[0]  # 高字节在前
            words.append(word)
        
        return {
            'success': True,
            'words': words,
            'data_hex': data.hex().upper()
        }

    @api.model
    def write_data(self, ip, port, epc_hex, mem_bank, word_ptr, write_data, 
                   address=0x00, pwd=0x00000000, mask_addr=None, mask_len=None):
        """
        写数据 (0x03)
        :param write_data: 要写入的数据（字列表）
        """
        try:
            epc_bytes = bytes.fromhex(epc_hex)
        except ValueError:
            raise UserError(_("无效的EPC十六进制字符串"))
        
        # 构建写入数据
        wdt_bytes = b''
        for word in write_data:
            wdt_bytes += struct.pack('>H', word)  # 高字节在前
        
        # 构建数据字段
        enum = len(epc_bytes) // 2
        wnum = len(write_data)
        
        data_field = struct.pack('<BB', wnum, enum) + epc_bytes
        data_field += struct.pack('<BB', mem_bank, word_ptr)
        data_field += wdt_bytes
        data_field += struct.pack('<I', pwd)
        
        if mask_addr is not None and mask_len is not None:
            data_field += struct.pack('<BB', mask_addr, mask_len)
        
        command_frame = self._build_frame(address, 0x03, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    @api.model
    def write_epc(self, ip, port, epc_hex, address=0x00, pwd=0x00000000):
        """
        写EPC号 (0x04)
        """
        try:
            epc_bytes = bytes.fromhex(epc_hex)
        except ValueError:
            raise UserError(_("无效的EPC十六进制字符串"))
        
        enum = len(epc_bytes) // 2
        data_field = struct.pack('<B', enum) + struct.pack('<I', pwd) + epc_bytes
        
        command_frame = self._build_frame(address, 0x04, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    @api.model
    def kill_tag(self, ip, port, epc_hex, kill_pwd, address=0x00, 
                 mask_addr=None, mask_len=None):
        """
        销毁标签 (0x05)
        """
        try:
            epc_bytes = bytes.fromhex(epc_hex)
        except ValueError:
            raise UserError(_("无效的EPC十六进制字符串"))
        
        enum = len(epc_bytes) // 2
        data_field = struct.pack('<B', enum) + epc_bytes
        data_field += struct.pack('<I', kill_pwd)
        
        if mask_addr is not None and mask_len is not None:
            data_field += struct.pack('<BB', mask_addr, mask_len)
        
        command_frame = self._build_frame(address, 0x05, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    # ==================== 读写器自定义命令 ====================
    
    @api.model
    def get_reader_info(self, ip, port, address=0x00):
        """
        读取读写器信息 (0x21)
        """
        command_frame = self._build_frame(address, 0x21)
        response_frame = self._send_command(ip, port, command_frame)
        result = self._parse_response(response_frame)
        
        if result['success'] and len(result['data']) >= 9:
            data = result['data']
            version = struct.unpack('>H', data[0:2])[0]
            reader_type = data[2]
            tr_type = data[3]
            dmaxfre = data[4]
            dminfre = data[5]
            power = data[6]
            scntm = data[7]
            
            return {
                'success': True,
                'version': f"{version >> 8}.{version & 0xFF}",
                'reader_type': reader_type,
                'protocol_support': {
                    '6c': bool(tr_type & 0x02),
                    '6b': bool(tr_type & 0x01)
                },
                'frequency_range': {
                    'max': dmaxfre,
                    'min': dminfre
                },
                'power': power,
                'scan_time': scntm
            }
        else:
            return {
                'success': False,
                'error': result['status_text']
            }

    @api.model
    def set_frequency(self, ip, port, max_freq, min_freq, address=0x00):
        """
        设置读写器工作频率 (0x22)
        """
        data_field = struct.pack('<BB', max_freq, min_freq)
        command_frame = self._build_frame(address, 0x22, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    @api.model
    def set_address(self, ip, port, new_address, address=0x00):
        """
        设置读写器地址 (0x24)
        """
        if new_address == 0xFF:
            raise UserError(_("地址不能设置为0xFF"))
        
        data_field = struct.pack('<B', new_address)
        command_frame = self._build_frame(address, 0x24, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    @api.model
    def set_scan_time(self, ip, port, scan_time, address=0x00):
        """
        设置读写器询查时间 (0x25)
        """
        if scan_time < 3 or scan_time > 255:
            raise UserError(_("询查时间范围：3-255 (对应300ms-25.5s)"))
        
        data_field = struct.pack('<B', scan_time)
        command_frame = self._build_frame(address, 0x25, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    @api.model
    def set_power(self, ip, port, power, address=0x00):
        """
        调整功率 (0x2F)
        """
        if power < 0 or power > 30:
            raise UserError(_("功率范围：0-30"))
        
        data_field = struct.pack('<B', power)
        command_frame = self._build_frame(address, 0x2F, data_field)
        response = self._send_command(ip, port, command_frame)
        result = self._parse_response(response)
        
        return {
            'success': result['success'],
            'error': result['status_text'] if not result['success'] else None
        }

    # ==================== 设备连接和状态 ====================
    
    @api.model
    def connect_device(self, ip, port):
        """连接设备"""
        if not ip or not port:
            return {'success': False, 'error': _("IP地址和端口不能为空")}
        return {'success': True, 'message': _("UHFReader18设备参数已配置")}

    @api.model
    def get_device_status(self, ip, port):
        """获取设备状态"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect((ip, port))
            
            # 尝试获取读写器信息
            info_result = self.get_reader_info(ip, port)
            
            return {
                'connected': True,
                'device_name': 'UHFReader18',
                'firmware_version': info_result.get('version', 'N/A'),
                'mode': 'network',
                'message': _("设备已连接并可达"),
                'reader_info': info_result
            }
        except socket.error as e:
            return {
                'connected': False,
                'device_name': 'UHFReader18',
                'firmware_version': 'N/A',
                'mode': 'network',
                'error': _("无法连接到设备: %s") % e
            }

    # ==================== 抽象方法实现 ====================
    
    def write_rfid_tag(self, data):
        """写入RFID标签"""
        _logger.warning("UHFReader18Service 不直接支持通用 write_rfid_tag 方法，请使用特定命令。")
        return {'success': False, 'error': _("UHFReader18Service 不直接支持通用写入方法。")}

    def read_rfid_tag(self):
        """读取RFID标签"""
        _logger.warning("UHFReader18Service 不直接支持通用 read_rfid_tag 方法，请使用 inventory_tags 或 read_data。")
        return {'success': False, 'error': _("UHFReader18Service 不直接支持通用读取方法。")}

    def verify_rfid_tag(self, rfid_number):
        """验证RFID标签"""
        _logger.warning("UHFReader18Service 不直接支持通用 verify_rfid_tag 方法。")
        return super().verify_rfid_tag(rfid_number)

    def erase_rfid_tag(self):
        """擦除RFID标签"""
        _logger.warning("UHFReader18Service 不支持擦除 RFID 标签。")
        return {'success': False, 'error': _("UHFReader18Service 不支持擦除 RFID 标签。")}
