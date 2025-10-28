# UHFReader18 TCP/IP 通信实现完整版

## 📖 概述

本实现基于UHFReader18用户手册v2.0，为您的RFID模块添加了完整的UHFReader18设备TCP/IP通信支持。虽然设备文档说明使用RS232/RS485接口，但通过TCP/IP连接是更灵活和现代的解决方案。

## 🚀 快速开始

### 1. 配置设备连接

1. 进入 **RFID → 配置 → UHFReader18 配置**
2. 填写设备信息：
   - **设备名称**: `UHFReader18-001`
   - **设备IP地址**: `10.0.97.186` (您的设备IP)
   - **设备端口**: `6000` (您的设备端口)
   - **设备地址**: `0` (RS485网络地址)
   - **连接超时**: `5` 秒

3. 点击 **"测试连接"** 验证连接
4. 点击 **"测试询查"** 验证标签检测
5. 点击 **"保存配置"** 保存设置

### 2. 功能演示

1. 进入 **RFID → 配置 → UHFReader18 演示**
2. 选择已配置的设备
3. 选择演示操作：
   - **询查标签**: 检测范围内的RFID标签
   - **读取标签数据**: 读取标签中的数据
   - **写入标签数据**: 向标签写入数据
   - **写入EPC号**: 修改标签EPC号
   - **销毁标签**: 永久销毁标签
   - **读取读写器信息**: 获取设备信息
   - **设置功率**: 调整设备功率
   - **设置询查时间**: 调整询查时间

4. 点击 **"运行演示"** 执行操作

## 🔧 技术实现

### 通信协议适配

```python
# 原始RS232/RS485协议通过TCP/IP传输
# 帧格式: [Len][Adr][Cmd][Data][CRC16]

class UHFReader18Service:
    def _build_frame(self, address, command, data=b''):
        # 构建通信帧
        frame_data = struct.pack('<BB', address, command) + data
        frame_len = len(frame_data) + 1 + 2  # +1 for Len itself, +2 for CRC
        
        # CRC计算范围：从Len到Data（不含CRC自身）
        crc_data = struct.pack('<B', frame_len) + frame_data
        crc = self._crc16(crc_data)
        
        # CRC低字节在前，高字节在后
        crc_bytes = struct.pack('<H', crc)
        
        return struct.pack('<B', frame_len) + frame_data + crc_bytes
```

### CRC16校验

```python
def _crc16(self, data):
    """CRC16校验 (多项式 0x8408, 初值 0xFFFF) - 按照用户手册算法"""
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
```

### 主要命令实现

#### 1. 询查标签 (0x01)

```python
def inventory_tags(self, ip, port, address=0x00, tid_addr=None, tid_len=None):
    """询查标签"""
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
```

#### 2. 读数据 (0x02)

```python
def read_data(self, ip, port, epc_hex, mem_bank, word_ptr, num_words, 
              address=0x00, pwd=0x00000000, mask_addr=None, mask_len=None):
    """读取标签数据"""
    epc_bytes = bytes.fromhex(epc_hex)
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
```

#### 3. 写数据 (0x03)

```python
def write_data(self, ip, port, epc_hex, mem_bank, word_ptr, write_data, 
               address=0x00, pwd=0x00000000, mask_addr=None, mask_len=None):
    """写入标签数据"""
    epc_bytes = bytes.fromhex(epc_hex)
    
    # 构建写入数据
    wdt_bytes = b''
    for word in write_data:
        wdt_bytes += struct.pack('>H', word)  # 高字节在前
    
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
```

#### 4. 写EPC号 (0x04)

```python
def write_epc(self, ip, port, epc_hex, address=0x00, pwd=0x00000000):
    """写入EPC号"""
    epc_bytes = bytes.fromhex(epc_hex)
    enum = len(epc_bytes) // 2
    data_field = struct.pack('<B', enum) + struct.pack('<I', pwd) + epc_bytes
    
    command_frame = self._build_frame(address, 0x04, data_field)
    response = self._send_command(ip, port, command_frame)
    result = self._parse_response(response)
    
    return {
        'success': result['success'],
        'error': result['status_text'] if not result['success'] else None
    }
```

#### 5. 销毁标签 (0x05)

```python
def kill_tag(self, ip, port, epc_hex, kill_pwd, address=0x00, 
             mask_addr=None, mask_len=None):
    """销毁标签"""
    epc_bytes = bytes.fromhex(epc_hex)
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
```

### 读写器自定义命令

#### 1. 读取读写器信息 (0x21)

```python
def get_reader_info(self, ip, port, address=0x00):
    """读取读写器信息"""
    command_frame = self._build_frame(address, 0x21)
    response = self._send_command(ip, port, command_frame)
    result = self._parse_response(response)
    
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
```

#### 2. 设置功率 (0x2F)

```python
def set_power(self, ip, port, power, address=0x00):
    """调整功率"""
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
```

#### 3. 设置询查时间 (0x25)

```python
def set_scan_time(self, ip, port, scan_time, address=0x00):
    """设置读写器询查时间"""
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
```

## 📋 使用示例

### Python代码示例

```python
# 创建服务实例
service = self.env['uhf.reader18.service']

# 询查标签
result = service.inventory_tags('10.0.97.186', 6000)
if result['success']:
    print(f"检测到 {result['num_tags']} 个标签")
    for epc_info in result['epc_list']:
        print(f"EPC: {epc_info['epc']} (长度: {epc_info['length']}字节)")

# 读取标签数据
result = service.read_data(
    '10.0.97.186', 6000, 
    'E20000123456789012345678',  # EPC
    0x03,  # User存储区
    0,     # 起始字地址
    4      # 读取4个字
)
if result['success']:
    print(f"读取到数据: {result['data_hex']}")

# 写入标签数据
write_words = [0x1234, 0x5678, 0xABCD, 0xEF00]
result = service.write_data(
    '10.0.97.186', 6000,
    'E20000123456789012345678',  # EPC
    0x03,  # User存储区
    0,     # 起始字地址
    write_words  # 要写入的字数据
)
if result['success']:
    print("写入成功")

# 获取读写器信息
result = service.get_reader_info('10.0.97.186', 6000)
if result['success']:
    print(f"版本: {result['version']}")
    print(f"功率: {result['power']}")
    print(f"协议支持: {result['protocol_support']}")
```

### Odoo集成示例

```python
# 在质检过程中使用
def generate_rfid_tag(self):
    """生成RFID标签"""
    service = self.env['uhf.reader18.service']
    
    # 询查标签
    inventory_result = service.inventory_tags('10.0.97.186', 6000)
    
    if inventory_result['success'] and inventory_result['epc_list']:
        epc = inventory_result['epc_list'][0]['epc']
        
        # 写入数据
        write_data = {
            'rfid_number': 'RFID000001',
            'product_code': 'PROD001',
            'lot_number': 'LOT001',
            'production_date': fields.Datetime.now(),
        }
        
        # 构建写入字数据
        data_str = f"{write_data['rfid_number']}|{write_data['product_code']}|{write_data['lot_number']}|{write_data['production_date']}"
        data_bytes = data_str.encode('utf-8')
        
        # 转换为字数据
        words = []
        for i in range(0, len(data_bytes), 2):
            if i + 1 < len(data_bytes):
                word = (data_bytes[i] << 8) | data_bytes[i + 1]
            else:
                word = data_bytes[i] << 8
            words.append(word)
        
        result = service.write_data('10.0.97.186', 6000, epc, 0x03, 0, words)
        
        if result['success']:
            # 创建RFID标签记录
            rfid_tag = self.env['rfid.tag'].create({
                'name': write_data['rfid_number'],
                'product_id': self.product_id.id,
                'stock_prod_lot_id': self.lot_id.id,
                'production_id': self.production_id.id,
            })
            
            return rfid_tag
        else:
            raise UserError(f"RFID写入失败: {result['error']}")
    else:
        raise UserError("未检测到RFID标签")
```

## 🔍 故障排除

### 常见问题

1. **连接失败**
   - 检查设备IP地址和端口
   - 确认设备已开机并连接到网络
   - 检查防火墙设置

2. **CRC校验失败**
   - 检查数据格式是否正确
   - 确认CRC16算法实现
   - 验证字节序（低字节在前）

3. **询查无标签**
   - 确认标签在读取范围内
   - 检查标签频率是否匹配
   - 调整设备功率设置

4. **读写失败**
   - 检查标签是否可写
   - 确认存储区地址正确
   - 验证数据长度
   - 检查访问密码是否正确

5. **状态码错误**
   - 0x05: 访问密码错误
   - 0x0B: 电子标签不支持该命令
   - 0xFA: 有电子标签，但通信不畅
   - 0xFB: 无电子标签可操作

### 调试技巧

```python
# 启用详细日志
import logging
logging.getLogger('odoo.addons.xq_rfid').setLevel(logging.DEBUG)

# 查看原始通信数据
def _send_command(self, ip, port, frame, timeout=5):
    print(f"发送: {frame.hex()}")
    
    response = self._send_command(ip, port, frame)
    print(f"接收: {response.hex()}")
    
    return response
```

## 📚 扩展功能

### 自定义存储格式

```python
def write_custom_data(self, epc, rfid_data):
    """写入自定义格式数据"""
    # 自定义数据格式
    data_format = {
        'rfid_number': rfid_data['rfid_number'],
        'product_code': rfid_data['product_code'],
        'lot_number': rfid_data['lot_number'],
        'production_date': rfid_data['production_date'].strftime('%Y%m%d'),
        'checksum': self._calculate_checksum(rfid_data)
    }
    
    # 转换为字节
    data_bytes = json.dumps(data_format).encode('utf-8')
    
    # 转换为字数据
    words = []
    for i in range(0, len(data_bytes), 2):
        if i + 1 < len(data_bytes):
            word = (data_bytes[i] << 8) | data_bytes[i + 1]
        else:
            word = data_bytes[i] << 8
        words.append(word)
    
    # 写入标签
    return self.write_data('10.0.97.186', 6000, epc, 0x03, 0, words)
```

### 批量操作

```python
def batch_read_tags(self, max_tags=10):
    """批量读取标签"""
    result = self.inventory_tags('10.0.97.186', 6000)
    
    if not result['success']:
        return result
    
    tags = result['epc_list'][:max_tags]
    results = []
    
    for epc_info in tags:
        try:
            read_result = self.read_data(
                '10.0.97.186', 6000, epc_info['epc'], 0x03, 0, 4
            )
            results.append({
                'epc': epc_info['epc'],
                'data': read_result,
                'success': True
            })
        except Exception as e:
            results.append({
                'epc': epc_info['epc'],
                'error': str(e),
                'success': False
            })
    
    return results
```

## 🎯 最佳实践

1. **连接管理**
   - 使用连接池管理多个设备
   - 实现自动重连机制
   - 设置合理的超时时间

2. **错误处理**
   - 实现重试机制
   - 记录详细错误日志
   - 提供用户友好的错误信息

3. **性能优化**
   - 批量操作减少通信次数
   - 缓存设备状态信息
   - 异步处理长时间操作

4. **安全考虑**
   - 验证数据完整性
   - 实现访问控制
   - 加密敏感数据

## 📖 协议参考

### 命令状态码

| 状态码 | 含义 | 说明 |
|--------|------|------|
| 0x00 | 操作成功 | 命令执行成功 |
| 0x01 | 询查时间结束前返回 | 询查命令在时间结束前返回 |
| 0x02 | 指定的询查时间溢出 | 询查时间超时 |
| 0x03 | 本条消息之后，还有消息 | 数据分多次发送 |
| 0x04 | 读写器存储空间已满 | 标签数量超过存储容量 |
| 0x05 | 访问密码错误 | 密码验证失败 |
| 0x09 | 销毁标签失败 | 销毁操作失败 |
| 0x0A | 销毁密码不能为全0 | 销毁密码无效 |
| 0x0B | 电子标签不支持该命令 | 标签不支持此操作 |
| 0xFA | 有电子标签，但通信不畅 | 通信质量差 |
| 0xFB | 无电子标签可操作 | 范围内无标签 |
| 0xFC | 电子标签返回错误代码 | 标签返回错误 |
| 0xFD | 命令长度错误 | 命令格式错误 |
| 0xFE | 不合法的命令 | 命令不存在 |
| 0xFF | 参数错误 | 参数不符合要求 |

### 存储区说明

| 存储区 | 代码 | 说明 | 读写权限 |
|--------|------|------|----------|
| 保留区 | 0x00 | 密码区 | 可读可写 |
| EPC区 | 0x01 | EPC号存储 | 可读可写 |
| TID区 | 0x02 | 标签ID | 只读 |
| User区 | 0x03 | 用户数据 | 可读可写 |

---

**UHFReader18 TCP/IP通信实现完成！** 🎉

现在您可以通过TCP/IP连接UHFReader18设备，实现完整的RFID标签管理功能，包括询查、读取、写入、销毁等所有操作。
