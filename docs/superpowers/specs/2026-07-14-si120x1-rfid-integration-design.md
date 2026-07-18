# SI120X1 RFID 集成设计

## 1. 状态

- 日期：2026-07-14
- Odoo 版本：18
- 目标模块：`xq_rfid`
- 现场设备型号：SI120X1
- 设计状态：已批准
- 实施原则：先完整移除 UHFReader18Service，再集成 SI120X1

## 2. 背景与已确认事实

当前模块实现了一套名为 UHFReader18 的旧 TCP 二进制协议，核心实现位于 `xq_rfid/models/uhf_reader18_client.py`。厂商 X 系列开发包中可以确认存在以下接口族：

1. EX10 原始模块协议；
2. ModuleAPI HTTP/JSON API；
3. Linux x86/x86-64 `libModuleAPI.so`；
4. 主动 TCP、HTTP 和 MQTT 上传；
5. E710UR4 专用 SDK。

旧 UHFReader18 协议与 EX10 协议在帧结构、长度定义、CRC、字节序、状态码和命令码方面不兼容。部分相同命令码的含义不同，错误复用可能触发非预期标签操作。因此 SI120X1 不得调用旧 UHFReader18 服务。

现有资料尚未提供 SI120X1 与 EX10、ModuleAPI、E710UR4 之间的直接型号映射。最终驱动必须依据实机探测或供应商针对 SI120X1 的明确资料选择，不能依据名称猜测。

## 3. 目标与成功标准

本项目完成两个连续目标：

1. 从 `xq_rfid` 完整移除 UHFReader18Service 及其所有专用入口、视图、ACL、文档、测试和业务引用；
2. 建立 SI120X1 的安全集成边界，并在实机确认接口后实现唯一匹配的驱动，完成质检写标签最小闭环。

成功标准：

- 安装或升级 `xq_rfid` 时不存在失效的 Python 导入、XML ID、ACL model ID 或前端资产引用；
- 生产代码、视图、ACL 和运行时配置中不存在 UHFReader18Service 调用链；迁移脚本可保留旧 selection 字面值，仅用于识别和停用历史记录；
- 已有旧设备记录不会被自动转换并向 SI120X1 发送旧协议命令；
- SI120X1 能完成连接测试、设备信息读取、有界盘存、EPC/TID 读取、User Bank 写入和写后读回；
- 只有在写后验证成功时，RFID 写入型质检才允许完成；
- 同一物理设备的操作严格串行，重复业务请求具备幂等性；
- 多公司、权限、目标设备和网络访问边界得到服务端校验。

## 4. 非目标

第一阶段不实现：

- Kill；
- 标签永久锁定；
- 固件升级；
- 恢复出厂配置；
- 长时间持续盘存；
- 主动 TCP/MQTT 事件采集；
- 同时支持开发包中的所有设备族；
- 在没有许可的情况下把厂商 `.so`、`.dll`、`.jar` 或 `.aar` 提交到 addon；
- 自动迁移旧 UHFReader18 设备为 SI120X1。

## 5. UHFReader18Service 移除范围

### 5.1 删除专用文件

删除：

- `xq_rfid/models/uhf_reader18_client.py`
- `xq_rfid/wizard/uhf_reader18_wizard.py`
- `xq_rfid/wizard/uhf_reader18_wizard_views.xml`
- `xq_rfid/tests/test_uhf_reader18.py`
- `xq_rfid/fix_work_mode.py`
- `xq_rfid/UHFReader18_TCP_使用说明.md`
- 仅服务于旧工作模式补丁或旧向导的说明文件

删除文件前逐一检查内容；与其他设备无关的公共说明应迁移到通用文档，而不是随文件丢弃。

### 5.2 清理导入、manifest 和 ACL

必须清理：

- `xq_rfid/models/__init__.py` 中的旧客户端导入；
- `xq_rfid/wizard/__init__.py` 中的旧向导导入；
- `xq_rfid/__manifest__.py` 中旧向导视图加载项；
- `xq_rfid/security/ir.model.access.csv` 中以下 model ACL：
  - `model_uhf_reader18_service`
  - `model_uhf_reader18_config_wizard`
  - `model_uhf_reader18_demo_wizard`
- 菜单、action、按钮、帮助文本、翻译和文档中的旧 XML ID 或名称。

### 5.3 清理业务调用

必须删除或重写：

- `quality_check.py` 中 `_write_to_uhf_reader18()` 及旧服务调用；
- `rfid_device.py` 中把普通网络设备交给 `uhf.reader18.service` 的分派；
- `quality_point.py` 中以 `device_type = 'uhf_reader18'` 查找默认设备的逻辑；
- `rfid_read_wizard.py` 中旧工作模式检测、切换及旧 `read_data()` 调用；
- `rfid_device_views.xml` 中 UHFReader18 专用字段、操作按钮和说明。

业务层不得暂时回退到默认模拟成功服务。SI120X1 驱动未就绪时，要求真实设备的质检必须明确失败并说明未配置驱动。

### 5.4 旧数据处理

对数据库中既有 `device_type = 'uhf_reader18'` 的设备记录采用安全失效策略：

- 保留名称、IP、端口、公司和历史统计；
- 设置为停用或“需要重新配置”；
- 清除不再有效的协议选择；
- 不自动改为 SI120X1；
- 管理员重新选择 SI120X1 驱动并通过连接测试后才能启用；
- 升级逻辑必须幂等，可重复执行。

若旧 selection 值受数据库约束影响，应先提供可安全读取旧值的过渡升级步骤，再从字段选择中移除，避免升级期间产生无效 selection 错误。

## 6. SI120X1 架构

采用独立 RFID Adapter 加可插拔驱动：

```text
Odoo 18
  -> SI120X1 领域服务
     -> 经认证的内部 HTTP 或 Unix socket
        -> RFID Adapter
           -> 单设备队列与状态机
              -> HTTP Driver / ModuleAPI SDK Driver / EX10 Driver
                 -> SI120X1
```

### 6.1 不在 Odoo worker 中直接加载 SDK 的原因

- Odoo 多 worker 可能同时持有同一设备；
- 厂商 SDK 同一设备句柄不能假定线程安全；
- Odoo master fork 后不能共享原生句柄、socket 或 mutex；
- 原生库崩溃不应导致 Odoo worker 崩溃；
- 长时间硬件 I/O 不应占用普通 HTTP worker；
- 设备重连、队列和盘存生命周期更适合常驻服务。

### 6.2 驱动选择门槛

按以下顺序探测，但一次部署只启用实机验证通过的驱动：

1. `ModuleAPI HTTP`：实机存在 `/moduleapi` 且连接、盘存、读写验证通过时采用；
2. `ModuleAPI SDK`：SI120X1 可被 Linux x86-64 `libModuleAPI.so` 识别时，在 Adapter 进程加载；
3. `EX10 raw`：只有抓包或厂商资料证明 SI120X1 端口透明承载 EX10 帧时采用；
4. 都不满足：停止集成，取得 SI120X1 专用协议或 Linux SDK。

严禁通过依次发送不同协议的写命令来试探设备。能力探测只允许使用无状态的连接、版本或设备信息操作。

## 7. Odoo 数据模型

### 7.1 `rfid.device.config`

连接方式和协议族分开建模。

建议协议族：

- `si120x1_moduleapi_http`
- `si120x1_moduleapi_sdk`
- `si120x1_ex10_raw`

建议传输类型：

- `http`
- `tcp_transparent`
- `serial`
- `sdk_tcp`
- `sdk_serial`

设备记录至少包含：

- `company_id`；
- Adapter 内部设备 ID；
- 主机和端口，仅管理员可维护；
- 协议族与传输类型；
- 固件、硬件和模块版本；
- 天线数和区域；
- 最近连接测试时间；
- 最近成功操作时间；
- 最近错误码和脱敏错误摘要；
- 是否支持 EPC、TID、User Bank 读写；
- 配置验证状态。

业务 RPC 不接受任意 IP、端口、SDK 路径或原始命令帧。业务层只能引用有权访问的设备记录。

### 7.2 `rfid.operation`

外部硬件写入不能随 PostgreSQL 事务回滚，因此使用持久操作状态机：

- `queued`
- `claimed`
- `inventorying`
- `writing`
- `verifying`
- `succeeded`
- `failed_retryable`
- `failed_manual`
- `cancelled`

操作记录包含：

- 全局唯一 `request_id`；
- 公司、设备、质检、批次和 RFID 标签；
- 操作类型；
- 目标 EPC/TID 的脱敏值和安全哈希；
- 载荷版本和 Token；
- 当前状态；
- 设备状态码；
- 尝试次数和时间戳；
- 写后验证结果。

`request_id` 建立唯一约束，重复提交返回已有操作，不能再次写标签。第一版请求 ID 由服务端按 `quality.check` 记录 ID、操作类型和载荷格式版本确定性生成；同一质检和同一格式版本始终得到同一请求 ID。只有管理员显式创建新的补救操作时，才生成新的请求 ID。

## 8. Adapter 契约

Odoo 只调用领域接口：

- `test_connection(device_id)`
- `get_device_info(device_id)`
- `inventory(device_id, duration_ms, include_tid)`
- `read_memory(device_id, target, bank, word_offset, word_count)`
- `write_memory(device_id, target, bank, word_offset, payload, request_id)`
- `write_and_verify(device_id, target, payload, request_id)`
- `get_operation(request_id)`

统一错误类型：

- `configuration_error`
- `authentication_error`
- `connection_error`
- `timeout`
- `protocol_error`
- `device_error`
- `no_tag`
- `multiple_tags`
- `target_changed`
- `unsupported_memory`
- `capacity_exceeded`
- `write_uncertain`
- `verification_failed`

返回必须保留原始设备状态码，但不得向普通用户暴露密码或原始敏感帧。

## 9. 单设备串行化

每台物理设备只有一个 Adapter 所有者和一个命令队列：

```text
DISCONNECTED
-> CONNECTING
-> IDLE
-> INVENTORYING
-> WRITING
-> VERIFYING
-> IDLE
```

异常进入 `ERROR` 或 `RECONNECT_WAIT`。规则如下：

- 同一设备不得并发执行命令；
- inventory 结束或超时后必须停止并恢复 IDLE；
- 写操作执行期间禁止其他盘存请求插队；
- Adapter 重启后根据持久 `request_id` 查询和读回，不能盲目重写；
- 多 Adapter 副本部署时使用数据库租约或分布式锁保证单一所有权，不能只用进程内锁。

## 10. 标签身份和载荷

### 10.1 标签目标

写前执行有界盘存并收集唯一 EPC/TID。必须满足：

- 现场仅有一个唯一标签；
- 读取到稳定 EPC；
- 设备支持时必须读取 TID；
- inventory 与写入之间目标未变化；
- 写命令使用 EPC/TID 过滤，而不是列表第一项。

单标签盘存只返回第一个标签，不能证明现场没有第二个标签；因此量产写入应使用能收集完整集合的短时盘存。

### 10.2 固定载荷

User Bank 第一版载荷固定为 24 字节：

```text
Magic     2 bytes  "XQ"
Version   1 byte   0x01
Flags     1 byte
Token    16 bytes
CRC32     4 bytes
```

完整产品、批次、工单和质检信息只保存在 Odoo，通过 Token 关联 `rfid.tag`。写前验证：

- 标签存在 User Bank；
- 容量至少 12 Word；
- word offset 合法；
- 访问密码配置有效；
- 编码和字节序与驱动契约一致。

`rfid.tag` 保存 EPC、TID、Token、格式版本、最后验证时间、写入设备和 `rfid.operation`。

## 11. 质检数据流

```text
用户请求质检通过
-> Odoo 校验权限、公司、生产订单、成品批次和设备
-> 为业务动作生成稳定 request_id
-> 创建或复用 rfid.operation
-> Adapter 领取操作
-> 有界盘存并确认唯一标签
-> 读取 EPC/TID 与 User Bank 能力
-> 写入 24 字节载荷
-> 立即读回同一范围
-> 校验 Magic、Version、Token 和 CRC32
-> Adapter 标记 succeeded
-> Odoo 定时任务或显式状态同步取得成功结果
-> 在独立数据库事务中绑定 rfid.tag、批次、质检和设备
-> 通过带上下文防重入标记的内部方法调用标准 quality.check 通过流程
```

首次用户请求只负责校验并创建或复用 `rfid.operation`，返回“RFID 写入处理中”，不在 HTTP 请求中长时间等待设备。Odoo 定时任务按小批量领取待同步结果；成功结果调用专用内部完成方法，该方法验证操作仍属于当前质检、状态为 `succeeded`、标签身份未变化，并使用上下文标记防止再次创建 RFID 操作。失败结果保留质检未通过状态并显示可操作错误。该内部完成方法必须幂等，多次同步不会重复通过质检或重复绑定标签。

若硬件操作未成功，要求设备的 RFID 写入型质检不得通过。不得调用模拟服务返回成功。

数据库提交和硬件写入无法原子化，因此采用可恢复状态机和补偿逻辑：

- Odoo 在操作成功后提交业务绑定失败：下一次按 `request_id` 复用已有成功结果；
- 写响应超时：先重新读取目标区域；一致则成功，不一致则 `write_uncertain` 或受控重试；
- 目标标签变化：停止并标记 `target_changed`；
- 用户关闭页面：后台操作和状态记录继续，不依赖浏览器连接。

## 12. 重试规则

可安全重试：

- 连接；
- 获取设备信息；
- 盘存；
- 读取 Bank；
- 查询操作结果。

不得在响应丢失时盲目重试：

- 写 EPC；
- 写 User Bank；
- 锁；
- Kill。

第一阶段只开放 User Bank 写入。响应丢失时执行读回判断：

- 内容完全一致：标记成功；
- 内容仍是旧值且目标身份一致：允许一次受控重写；
- 内容部分变化、目标变化或无法确认：标记 `failed_manual`。

## 13. 安全设计

### 13.1 Odoo

- 所有公开硬件方法显式校验 RFID 权限；
- `rfid.device.config` 增加 `company_id` 和多公司 record rule；
- 质检点只能选择同公司且验证通过的设备；
- 普通用户不得提交网络目标、SDK 路径或原始指令；
- 设备诊断与业务写入权限分离；
- 危险操作第一阶段不存在于模型和 UI。

### 13.2 Adapter

同机优先 Unix socket。跨主机使用 TLS 或 mTLS，并实施：

- 调用方身份验证；
- 设备白名单；
- 请求签名和时间戳；
- 防重放；
- 每设备限流；
- 请求体大小限制；
- SDK 路径由部署配置固定，不能由 Odoo 请求指定。

### 13.3 日志

禁止记录：

- Access Password 和 Kill Password；
- 完整 User Bank 数据；
- 未脱敏完整命令帧；
- 原生结构体的任意内存内容。

允许记录：

- 请求 ID；
- 设备 ID；
- 命令类型；
- 状态码；
- 数据长度；
- 脱敏 EPC/TID；
- 耗时和重试次数。

## 14. 测试策略

### 14.1 删除回归测试

- Python 导入不存在旧模块引用；
- manifest 所有文件存在；
- XML 可解析且所有本模块 XML ID 引用有效；
- ACL 不引用已删除模型；
- 除迁移脚本、规格和历史说明外，生产代码、视图、ACL、manifest 和翻译中不存在 UHFReader18Service、旧向导模型和旧服务模型；
- 模块升级迁移会停用旧设备记录，且不会改变其他设备记录。

### 14.2 Adapter 契约测试

- 每个驱动映射到统一返回结构；
- 超时和设备错误正确分类；
- 同设备并发请求被串行化；
- 重复 `request_id` 不产生第二次写；
- Adapter 重启后可恢复未决操作；
- 日志脱敏。

### 14.3 协议测试

若选择 EX10：

- 使用厂商黄金向量验证 CRC16、长度和大端字段；
- 覆盖错误头、CRC 错误、状态码、命令不匹配、拆包、粘包和多帧；
- 覆盖盘存缓存、读写边界和写后验证。

若选择 ModuleAPI SDK：

- 校验 C ABI 结构大小、字段偏移和函数签名；
- Mock 动态库返回；
- 验证 Init、ParamSet、Inventory/GetNextTag、Stop、Close 顺序；
- 验证 SDK 崩溃和子进程退出不会影响 Odoo；
- 禁止回调线程直接访问 Odoo ORM。

若选择 HTTP：

- Mock HTTP 服务验证请求格式和错误映射；
- 覆盖非 2xx、无效 JSON、设备业务错误、超时和重复请求；
- 验证 URL 只能由设备配置构造，不能由 RPC 覆盖。

### 14.4 Odoo 测试

- 缺少成品批次时不能写标签或通过质检；
- 无标签和多标签时拒绝写入；
- 写后读回不一致时不能通过；
- 成功重放同一请求不会重复写；
- 后台同步成功结果时使用防重入上下文，不会再次创建 RFID 操作；
- 同一成功结果被多次同步时，不会重复通过质检或重复绑定标签；
- 公司 A 不能操作公司 B 的设备；
- 非授权用户不能调用诊断和写入方法；
- 设备未验证、停用或驱动缺失时明确失败；
- 不存在模拟成功路径。

### 14.5 SI120X1 实机验收

上线前记录并验证：

- 铭牌型号 SI120X1；
- 主板、模块、硬件和固件版本；
- 认证区域和天线数；
- 连接接口、IP/端口或串口参数；
- `/moduleapi` 是否存在；
- Linux ModuleAPI 是否识别；
- 是否透明承载 EX10；
- 目标标签 EPC、TID 和 User Bank 容量；
- 无标签、单标签、多标签；
- 断网、设备重启和 Adapter 重启；
- 写响应丢失后的读回；
- 连续写入稳定性和重复请求幂等性。

实机证据决定最终驱动，不允许以开发包中存在某 SDK 代替设备兼容性验证。

## 15. 发布与许可

当前开发包未发现明确的厂商二进制再分发许可。执行规则：

- 厂商二进制不进入 `xq_rfid` Git 仓库；
- 部署方在 Adapter 主机单独安装；
- 记录 SDK 文件版本、哈希、架构和安装路径；
- 容器镜像或客户安装包若包含 SDK，必须先取得书面授权；
- 如果无法取得授权，优先采用公开 HTTP 或经验证的原始协议实现。

## 16. 实施阶段

### 阶段 A：移除旧实现

1. 建立全引用清单；
2. 增加旧设备安全失效迁移；
3. 删除 UHFReader18 专用文件；
4. 清理 Python、XML、ACL、manifest、文档和翻译引用；
5. 重写通用向导和质检接口，使其在 SI120X1 未配置时明确失败；
6. 运行静态和升级回归测试。

### 阶段 B：SI120X1 能力确认

1. 只执行无状态的连接与版本探测；
2. 保存设备和固件证据；
3. 选择唯一驱动；
4. 明确供应商许可和部署架构。

### 阶段 C：质检最小闭环

1. 实现 Adapter 和单设备队列；
2. 实现选定驱动；
3. 实现 `rfid.operation` 幂等状态机；
4. 实现唯一标签识别；
5. 实现 24 字节载荷写入与读回；
6. 接入 `quality.check`；
7. 完成单元、契约、Odoo 和 SI120X1 实机测试。

## 17. 验收门槛

进入生产前必须同时满足：

- UHFReader18Service 全链路已移除且引用扫描为空；
- 旧设备记录已安全停用；
- SI120X1 的实际接口族有实机证据；
- 连接、盘存、读、写、读回均在目标固件上通过；
- 多标签拒绝、权限、多公司、幂等和错误恢复测试通过；
- Adapter 崩溃不会导致 Odoo worker 崩溃；
- 厂商二进制部署满足授权要求；
- 未启用 Kill、永久锁定或固件操作。
