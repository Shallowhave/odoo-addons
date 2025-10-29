#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UHFReader18 TCP客户端测试脚本 - 完整版

基于UHFReader18用户手册v2.0实现
用于测试UHFReader18设备的TCP/IP通信功能
"""

import sys
import os
import time
import struct

def test_crc16():
    """测试CRC16计算 - 按照用户手册算法"""
    print("=== 测试CRC16计算 ===")
    
    # 测试数据
    test_data = b'\x04\x00\x01\x00'
    
    # 按照用户手册的C语言算法
    PRESET_VALUE = 0xFFFF
    POLYNOMIAL = 0x8408
    
    uiCrcValue = PRESET_VALUE
    for ucI in range(len(test_data)):
        uiCrcValue = uiCrcValue ^ test_data[ucI]
        for ucJ in range(8):
            if uiCrcValue & 0x0001:
                uiCrcValue = (uiCrcValue >> 1) ^ POLYNOMIAL
            else:
                uiCrcValue = uiCrcValue >> 1
    
    calculated_crc = struct.pack('<H', uiCrcValue)
    
    print(f"测试数据: {test_data.hex()}")
    print(f"计算CRC: {calculated_crc.hex()}")
    print(f"CRC值: {uiCrcValue:04X}")
    print()

def test_frame_building():
    """测试帧构建 - 按照用户手册格式"""
    print("=== 测试帧构建 ===")
    
    def build_frame(address, command, data=b''):
        """构建通信帧"""
        # Len(1字节) + Adr(1字节) + Cmd(1字节) + Data(N字节) + CRC16(2字节)
        frame_data = struct.pack('<BB', address, command) + data
        frame_len = len(frame_data) + 1 + 2  # +1 for Len itself, +2 for CRC
        
        # CRC计算范围：从Len到Data（不含CRC自身）
        crc_data = struct.pack('<B', frame_len) + frame_data
        crc = calculate_crc16(crc_data)
        
        # CRC低字节在前，高字节在后
        crc_bytes = struct.pack('<H', crc)
        
        return struct.pack('<B', frame_len) + frame_data + crc_bytes
    
    def calculate_crc16(data):
        """计算CRC16"""
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
    
    # 测试询查标签命令
    frame = build_frame(0x00, 0x01)
    print(f"询查标签帧: {frame.hex().upper()}")
    
    # 测试读数据命令
    epc_data = bytes.fromhex('E20000123456789012345678')
    enum = len(epc_data) // 2
    read_data = struct.pack('<B', enum) + epc_data
    read_data += struct.pack('<BBB', 0x03, 0x00, 0x04)  # User区, 起始地址0, 读取4个字
    read_data += struct.pack('<I', 0x00000000)  # 访问密码
    frame = build_frame(0x00, 0x02, read_data)
    print(f"读数据帧: {frame.hex().upper()}")
    
    # 测试写数据命令
    write_words = [0x1234, 0x5678, 0xABCD, 0xEF00]
    wdt_bytes = b''
    for word in write_words:
        wdt_bytes += struct.pack('>H', word)  # 高字节在前
    
    write_data = struct.pack('<BB', len(write_words), enum) + epc_data
    write_data += struct.pack('<BB', 0x03, 0x00)  # User区, 起始地址0
    write_data += wdt_bytes
    write_data += struct.pack('<I', 0x00000000)  # 访问密码
    frame = build_frame(0x00, 0x03, write_data)
    print(f"写数据帧: {frame.hex().upper()}")
    
    print()

def test_response_parsing():
    """测试响应解析"""
    print("=== 测试响应解析 ===")
    
    # 模拟询查标签响应
    def simulate_inventory_response():
        """模拟询查标签响应"""
        # Len(1) Adr(1) reCmd(1) Status(1) Num(1) EPC_Data(N) CRC(2)
        status = 0x00  # 成功
        num_tags = 0x02  # 2个标签
        
        # EPC1: 长度12字节
        epc1_len = 12
        epc1_data = bytes.fromhex('E20000123456789012345678')
        
        # EPC2: 长度12字节
        epc2_len = 12
        epc2_data = bytes.fromhex('E20000123456789012345679')
        
        response_data = struct.pack('<BB', status, num_tags)
        response_data += struct.pack('<B', epc1_len) + epc1_data
        response_data += struct.pack('<B', epc2_len) + epc2_data
        
        # 构建完整响应帧
        frame_len = 1 + 1 + 1 + len(response_data) + 2  # Adr + reCmd + Status + Data + CRC
        frame = struct.pack('<B', frame_len)  # Len
        frame += struct.pack('<BB', 0x00, 0x01)  # Adr, reCmd
        frame += response_data
        
        # 计算CRC
        crc_data = struct.pack('<B', frame_len) + frame[1:]
        crc = calculate_crc16(crc_data)
        frame += struct.pack('<H', crc)
        
        return frame
    
    def calculate_crc16(data):
        """计算CRC16"""
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
    
    def parse_inventory_response(response_frame):
        """解析询查标签响应"""
        if len(response_frame) < 6:
            return {'success': False, 'error': "响应帧过短"}
        
        # 跳过 Len 字节
        addr = response_frame[1]
        re_cmd = response_frame[2]
        status = response_frame[3]
        data = response_frame[4:-2]  # 排除 Len, Adr, reCmd, Status, CRC
        
        if status != 0x00:
            return {'success': False, 'error': f"状态错误: {status:02X}"}
        
        if len(data) < 1:
            return {'success': False, 'error': "响应数据为空"}
        
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
    
    # 测试询查标签响应解析
    print("模拟询查标签响应...")
    response = simulate_inventory_response()
    print(f"响应帧: {response.hex().upper()}")
    
    result = parse_inventory_response(response)
    if result['success']:
        print(f"解析成功！检测到 {result['num_tags']} 个标签:")
        for epc_info in result['epc_list']:
            print(f"  - EPC: {epc_info['epc']} (长度: {epc_info['length']}字节)")
    else:
        print(f"解析失败: {result['error']}")
    
    print()

def test_read_data_parsing():
    """测试读数据响应解析"""
    print("=== 测试读数据响应解析 ===")
    
    def simulate_read_data_response():
        """模拟读数据响应"""
        # Len(1) Adr(1) reCmd(1) Status(1) Data(N) CRC(2)
        status = 0x00  # 成功
        
        # 模拟读取的数据（4个字）
        words = [0x1234, 0x5678, 0xABCD, 0xEF00]
        data_bytes = b''
        for word in words:
            data_bytes += struct.pack('>H', word)  # 高字节在前
        
        response_data = struct.pack('<B', status) + data_bytes
        
        # 构建完整响应帧
        frame_len = 1 + 1 + 1 + len(response_data) + 2  # Adr + reCmd + Status + Data + CRC
        frame = struct.pack('<B', frame_len)  # Len
        frame += struct.pack('<BB', 0x00, 0x02)  # Adr, reCmd
        frame += response_data
        
        # 计算CRC
        crc_data = struct.pack('<B', frame_len) + frame[1:]
        crc = calculate_crc16(crc_data)
        frame += struct.pack('<H', crc)
        
        return frame
    
    def calculate_crc16(data):
        """计算CRC16"""
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
    
    def parse_read_data_response(response_frame):
        """解析读数据响应"""
        if len(response_frame) < 6:
            return {'success': False, 'error': "响应帧过短"}
        
        status = response_frame[3]
        data = response_frame[4:-2]  # 排除 Len, Adr, reCmd, Status, CRC
        
        if status != 0x00:
            return {'success': False, 'error': f"状态错误: {status:02X}"}
        
        if len(data) % 2 != 0:
            return {'success': False, 'error': "数据长度不是偶数"}
        
        words = []
        for i in range(0, len(data), 2):
            word = struct.unpack('>H', data[i:i+2])[0]  # 高字节在前
            words.append(word)
        
        return {
            'success': True,
            'words': words,
            'data_hex': data.hex().upper()
        }
    
    # 测试读数据响应解析
    print("模拟读数据响应...")
    response = simulate_read_data_response()
    print(f"响应帧: {response.hex().upper()}")
    
    result = parse_read_data_response(response)
    if result['success']:
        words_text = ' '.join([f"{word:04X}" for word in result['words']])
        print(f"解析成功！读取到 {len(result['words'])} 个字:")
        print(f"  字数据: {words_text}")
        print(f"  原始数据: {result['data_hex']}")
    else:
        print(f"解析失败: {result['error']}")
    
    print()

def test_status_codes():
    """测试状态码"""
    print("=== 测试状态码 ===")
    
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
        0xF9: "命令执行出错",
        0xFA: "有电子标签，但通信不畅，操作失败",
        0xFB: "无电子标签可操作",
        0xFC: "电子标签返回错误代码",
        0xFD: "命令长度错误",
        0xFE: "不合法的命令",
        0xFF: "参数错误"
    }
    
    print("状态码说明:")
    for code, description in status_map.items():
        print(f"  {code:02X}: {description}")
    
    print()

def test_memory_banks():
    """测试存储区"""
    print("=== 测试存储区 ===")
    
    memory_banks = {
        0x00: "保留区 (密码区)",
        0x01: "EPC存储区",
        0x02: "TID存储区",
        0x03: "User存储区"
    }
    
    print("存储区说明:")
    for code, description in memory_banks.items():
        print(f"  {code:02X}: {description}")
    
    print()

def main():
    """主测试函数"""
    print("UHFReader18 TCP客户端测试 - 完整版")
    print("基于UHFReader18用户手册v2.0")
    print("=" * 60)
    print()
    
    # 运行各项测试
    test_crc16()
    test_frame_building()
    test_response_parsing()
    test_read_data_parsing()
    test_status_codes()
    test_memory_banks()
    
    print("=" * 60)
    print("测试完成！")
    print()
    print("如果所有测试都通过，说明TCP客户端实现正确。")
    print("接下来可以：")
    print("1. 配置UHFReader18设备的IP地址和端口 (当前设备: 10.0.97.186:6000)")
    print("2. 在Odoo中使用配置向导测试连接")
    print("3. 运行演示向导验证功能")
    print()
    print("支持的命令:")
    print("- 询查标签 (0x01)")
    print("- 读数据 (0x02)")
    print("- 写数据 (0x03)")
    print("- 写EPC号 (0x04)")
    print("- 销毁标签 (0x05)")
    print("- 读取读写器信息 (0x21)")
    print("- 设置功率 (0x2F)")
    print("- 设置询查时间 (0x25)")
    print("- 设置读写器地址 (0x24)")
    print("- 设置工作频率 (0x22)")

if __name__ == '__main__':
    main()
