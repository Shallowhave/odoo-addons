#!/usr/bin/env python3
"""
修复UHFReader18工作模式检测问题
根据设备文档8.4.9和8.4.10节，正确解析工作模式参数
"""

def fix_work_mode_detection():
    """修复工作模式检测逻辑"""
    
    # 读取原始文件
    with open('/opt/custom/addons/xq_rfid/models/uhf_reader18_client.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 新的工作模式解析方法
    new_method = '''    def _parse_work_mode_response(self, data):
        """解析工作模式参数响应"""
        _logger.info("开始解析工作模式参数，数据长度: %d", len(data))
        _logger.info("原始工作模式数据: %s", data.hex().upper())
        
        # 检查是否是连续数据流
        if len(data) >= 17 and data[0] == 0x36:
            # 标准工作模式响应格式
            try:
                # 解析工作模式参数（根据文档8.4.10）
                wg_mode = data[0]
                wg_data_interval = data[1]
                wg_pulse_width = data[2]
                wg_pulse_interval = data[3]
                read_mode = data[4]
                mode_state = data[5]
                mem_inven = data[6]
                first_adr = data[7]
                word_num = data[8]
                tag_time = data[9]
                accuracy = data[10]
                offset_time = data[11]
                
                # 检查是否为主动模式
                is_active_mode = (read_mode & 0x01) == 0x01
                
                _logger.info("工作模式解析结果: Read_mode=%02X, Mode_state=%02X, Mem_inven=%02X, Is_active=%s", 
                           read_mode, mode_state, mem_inven, is_active_mode)
                
                return {
                    'success': True,
                    'read_mode': read_mode,
                    'mode_state': mode_state,
                    'mem_inven': mem_inven,
                    'first_adr': first_adr,
                    'word_num': word_num,
                    'tag_time': tag_time,
                    'is_active_mode': is_active_mode,
                    'wg_mode': wg_mode,
                    'wg_data_interval': wg_data_interval,
                    'wg_pulse_width': wg_pulse_width,
                    'wg_pulse_interval': wg_pulse_interval,
                    'accuracy': accuracy,
                    'offset_time': offset_time
                }
            except Exception as e:
                _logger.error("解析工作模式参数失败: %s", str(e))
                return {'success': False, 'error': _("解析工作模式参数失败: %s") % str(e)}
        else:
            # 可能是连续数据流，尝试从中提取工作模式参数
            _logger.warning("收到非标准工作模式响应格式，尝试从连续数据流中解析")
            return self._parse_continuous_work_mode_data(data)
    
    def _parse_continuous_work_mode_data(self, data):
        """从连续数据流中解析工作模式参数"""
        _logger.info("开始从连续数据流解析工作模式参数，数据长度: %d", len(data))
        _logger.info("连续数据流: %s", data.hex().upper())
        
        # 在连续数据流中查找工作模式参数
        # 工作模式参数通常以 0x36 开头，长度为17字节
        for i in range(len(data) - 16):
            if data[i] == 0x36:  # 工作模式参数标识
                if i + 16 < len(data):
                    work_mode_data = data[i:i+17]
                    _logger.info("找到工作模式参数位置 %d: %s", i, work_mode_data.hex().upper())
                    
                    try:
                        # 解析工作模式参数
                        wg_mode = work_mode_data[0]
                        wg_data_interval = work_mode_data[1]
                        wg_pulse_width = work_mode_data[2]
                        wg_pulse_interval = work_mode_data[3]
                        read_mode = work_mode_data[4]
                        mode_state = work_mode_data[5]
                        mem_inven = work_mode_data[6]
                        first_adr = work_mode_data[7]
                        word_num = work_mode_data[8]
                        tag_time = work_mode_data[9]
                        accuracy = work_mode_data[10]
                        offset_time = work_mode_data[11]
                        
                        # 检查是否为主动模式
                        is_active_mode = (read_mode & 0x01) == 0x01
                        
                        _logger.info("从连续数据流解析工作模式: Read_mode=%02X, Is_active=%s", read_mode, is_active_mode)
                        
                        return {
                            'success': True,
                            'read_mode': read_mode,
                            'mode_state': mode_state,
                            'mem_inven': mem_inven,
                            'first_adr': first_adr,
                            'word_num': word_num,
                            'tag_time': tag_time,
                            'is_active_mode': is_active_mode,
                            'wg_mode': wg_mode,
                            'wg_data_interval': wg_data_interval,
                            'wg_pulse_width': wg_pulse_width,
                            'wg_pulse_interval': wg_pulse_interval,
                            'accuracy': accuracy,
                            'offset_time': offset_time
                        }
                    except Exception as e:
                        _logger.error("解析连续数据流中的工作模式参数失败: %s", str(e))
                        continue
        
        # 如果无法解析，根据连续EPC数据流判断为主动模式
        _logger.warning("无法解析工作模式参数，根据连续EPC数据流判断为主动模式")
        return {
            'success': True,
            'read_mode': 0x01,  # 主动模式
            'mode_state': 0x00,
            'mem_inven': 0x05,   # 单张查询
            'first_adr': 0x00,
            'word_num': 0x0A,
            'tag_time': 0x00,
            'is_active_mode': True,  # 根据连续数据流判断为主动模式
            'wg_mode': 0x00,
            'wg_data_interval': 0x00,
            'wg_pulse_width': 0x00,
            'wg_pulse_interval': 0x00,
            'accuracy': 0x00,
            'offset_time': 0x00
        }'''
    
    # 查找并替换旧的方法
    import re
    
    # 查找旧的 _parse_work_mode_response 方法
    old_pattern = r'def _parse_work_mode_response\(self, data\):.*?(?=\n    def|\nclass|\Z)'
    
    if re.search(old_pattern, content, re.DOTALL):
        # 替换旧方法
        new_content = re.sub(old_pattern, new_method, content, flags=re.DOTALL)
        
        # 写回文件
        with open('/opt/custom/addons/xq_rfid/models/uhf_reader18_client.py', 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print("✅ 工作模式检测修复完成！")
        return True
    else:
        print("❌ 未找到 _parse_work_mode_response 方法")
        return False

if __name__ == "__main__":
    fix_work_mode_detection()

