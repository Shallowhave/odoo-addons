# SI120X1 RFID Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先从 `xq_rfid` 安全移除 UHFReader18Service 全链路，再建立不依赖已确认厂商协议的 SI120X1 Adapter 边界、持久幂等操作状态机和 Odoo 18 质检写标签闭环；具体硬件驱动只在实机证据确认唯一接口族后实施。

**Architecture:** Odoo 只保存业务记录、设备白名单和 `rfid.operation`，通过认证的 Adapter 客户端提交或查询稳定 `request_id`；独立 Adapter 进程持久化请求并按物理设备串行执行有界盘存、写入和读回验证。阶段 A（旧驱动移除）和阶段 B（Adapter/Odoo 核心）各自可测试；阶段 C 使用实机证据从 ModuleAPI HTTP、ModuleAPI SDK、EX10 raw 中选择一个驱动，不自动探测写协议、不把厂商二进制提交到 addon。

**Tech Stack:** Odoo 18 ORM/ACL/record rules/cron/OWL、Python 3 标准库（`http.server`、`socketserver`、`sqlite3`、`threading`、`hmac`）、Odoo 已有 `requests 2.32.3`、JavaScript OWL、PostgreSQL；Adapter 第一版不新增 FastAPI、Flask、aiohttp、gunicorn 或 pytest 依赖。

## Global Constraints

- 目标模块固定为 `xq_rfid`，模块版本从 `18.0.1.0.0` 升级到 `18.0.2.0.0`。
- 先完成 UHFReader18Service 删除和旧数据安全失效，再开放 SI120X1 操作。
- 生产代码、视图、ACL 和运行时配置不得保留 `uhf.reader18.service`；迁移脚本仅可保留旧 selection 字面值用于识别历史数据。
- `device_type='uhf_reader18'` 的历史设备只停用并标记“需要重新配置”，不得自动转换为 SI120X1。
- SI120X1 具体驱动必须由无状态实机证据选定：ModuleAPI HTTP、ModuleAPI SDK、EX10 raw 三选一；不得发送写命令试探协议。
- 第一阶段只允许有界盘存、EPC/TID 读取、User Bank 读写和写后读回；不实现 Kill、锁定、写 EPC、固件升级、恢复出厂或持续主动盘存。
- User Bank 第一版载荷固定 24 字节：`b"XQ" + version(0x01) + flags(1 byte) + token(16 bytes) + CRC32(4 bytes, big-endian)`，共 12 Word。
- 第一版 `request_id` 必须由 `quality.check` ID、操作类型 `write_and_verify` 和载荷版本 `1` 确定性生成；补救操作只能由管理员显式创建新的请求 ID。
- 普通业务 RPC 只接受有权访问的 `rfid.device.config` 记录，不接受任意 IP、端口、SDK 路径、原始帧或访问密码。
- Odoo 与 Adapter 的共享密钥仅从部署环境或受限系统参数读取，不能写入设备记录、日志、chatter 或浏览器响应。
- HMAC timestamp 必须使用 UTC Unix seconds；nonce 为每请求至少 128-bit 随机值；签名覆盖实际 request-target（含 query string，如有），Odoo 与 Adapter 使用同一 canonicalization 函数的黄金向量测试。
- Access Password、Kill Password、完整 User Bank、未脱敏帧和原生内存不得写日志。
- Odoo worker 不直接加载厂商 `.so`；厂商 `.so/.dll/.jar/.aar` 不进入 addon、Git、容器或客户安装包，除非取得明确再分发授权。
- 需要真实设备的流程必须 fail-closed；不得用模拟成功替代硬件成功。
- 每个物理设备严格串行；多 Adapter 副本通过 SQLite 租约实现同一设备单一所有权，不能只依赖进程内锁。
- 运行任何连接数据库、升级模块、启动 Odoo 服务或访问 SI120X1 的命令前，执行者必须再次取得用户确认。
- 当前任务不得 commit 或 push；下述“提交”步骤仅列出建议边界，执行时一律跳过，除非用户以后明确授权。
- 不修改或提交当前工作区中与本任务无关的 `.codebase-memory/*` 和 `freeform_quant_delivery/` 变更。

## Plan Boundaries

本计划故意分为三个发布门槛：

1. **A — 旧实现移除（Tasks 1–4）**：交付可安装、无 UHFReader18 运行时引用且真实硬件路径 fail-closed 的 `xq_rfid`。
2. **B — SI120X1 Adapter 与 Odoo 核心（Tasks 5–13）**：交付可用 fake driver 完整验证的状态机、Odoo 数据模型、异步质检和 UI；不宣称已兼容实机。
3. **C — 唯一实机驱动（Task 14）**：是硬门槛任务。未取得 SI120X1 接口证据时不得开始，也不得把三种候选驱动都实现后在运行时猜测。

## Locked File Structure

### Odoo addon

- `xq_rfid/migrations/18.0.2.0/pre-disable-uhf-reader18.py`：升级前幂等停用旧设备，并把旧 selection 值改为安全的 `legacy_disabled`。
- `xq_rfid/models/rfid_device.py`：设备白名单、SI120X1 配置、能力与验证状态；只委托 `rfid.adapter.client`。
- `xq_rfid/models/rfid_adapter_client.py`：唯一的 Odoo→Adapter HTTP 客户端、签名、超时与错误分类。
- `xq_rfid/models/rfid_payload.py`：24 字节载荷编解码和 CRC32；不访问网络。
- `xq_rfid/models/rfid_operation.py`：Odoo 业务操作、稳定请求 ID、提交/查询/同步和 cron。
- `xq_rfid/models/rfid_tag.py`：物理 EPC/TID/Token/版本/最后验证信息。
- `xq_rfid/models/quality_check.py`：首次通过转为异步操作；成功同步后带 context guard 进入标准 Odoo 18 `do_pass()` 继承链。
- `xq_rfid/models/quality_point.py`：只选择同公司、active、验证通过且支持 User Bank 写入的 SI120X1。
- `xq_rfid/wizard/rfid_read_wizard.py`：管理员诊断只按设备记录调用 Adapter。
- `xq_rfid/data/rfid_operation_cron.xml`：小批量同步 Adapter 结果。
- `xq_rfid/tests/`：Odoo `TransactionCase`、纯 Python 单元测试和静态删除回归测试。

### Independent Adapter

独立服务必须位于 addon 之外，避免 Odoo import 或打包时加载服务代码：

- `services/xq_rfid_adapter/pyproject.toml`：仅声明本地可安装包和 Python 版本；运行时依赖保持为空。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/__main__.py`：CLI 和进程生命周期。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/config.py`：JSON 配置、设备白名单、环境密钥和大小/超时限制。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/api.py`：标准库 HTTP API、HMAC 认证、防重放和统一 JSON envelope。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/store.py`：SQLite 操作、设备租约、幂等 `request_id` 和崩溃恢复。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/domain.py`：统一请求/结果/错误数据结构。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/queue.py`：按设备 claim 与串行 worker。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/service.py`：盘存→唯一标签→写入→读回验证状态机。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/base.py`：驱动 Protocol 和统一能力对象。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/fake.py`：自动化测试专用驱动；生产配置拒绝启用。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/moduleapi_http.py`、`moduleapi_sdk.py` 或 `ex10_raw.py`：Task 14 中只创建一个经实机确认的文件。
- `services/xq_rfid_adapter/tests/`：标准库 `unittest`，不需要 Odoo 数据库或 pytest。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/README.md`：独立部署、配置、systemd、密钥轮换、许可和验收命令。
- `services/xq_rfid_adapter/src/xq_rfid_adapter/examples/config.example.json`：无真实地址或密钥的配置模板。

---

### Task 1: 建立删除回归测试并安全迁移旧设备

**Files:**
- Create: `xq_rfid/tests/__init__.py`
- Create: `xq_rfid/tests/test_legacy_removal.py`
- Create: `xq_rfid/migrations/18.0.2.0/pre-disable-uhf-reader18.py`
- Modify: `xq_rfid/__manifest__.py:40,63-80`
- Modify: `xq_rfid/models/rfid_device.py:175-197`

**Interfaces:**
- Consumes: 当前 `rfid_device_config` 表和历史 `device_type='uhf_reader18'` 值。
- Produces: `device_type='legacy_disabled'`（只表示历史停用设备）、`migration_required=True`、幂等 `migrate(cr, installed_version)`。

- [ ] **Step 1: 写纯静态失败测试，锁定允许和禁止的旧字符串位置**

```python
# xq_rfid/tests/test_legacy_removal.py
from pathlib import Path
import ast
import unittest

ADDON = Path(__file__).resolve().parents[1]
ALLOWED = {
    ADDON / "migrations/18.0.2.0/pre-disable-uhf-reader18.py",
}
FORBIDDEN_ROOTS = [
    ADDON / "models",
    ADDON / "wizard",
    ADDON / "views",
    ADDON / "security",
    ADDON / "static",
]
TOKENS = ("UHFReader18", "uhf_reader18", "uhf.reader18")


class TestLegacyRemoval(unittest.TestCase):
    def test_runtime_files_do_not_reference_legacy_driver(self):
        offenders = []
        for root in FORBIDDEN_ROOTS:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in {".py", ".xml", ".csv", ".js"}:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    if any(token in text for token in TOKENS):
                        offenders.append(str(path.relative_to(ADDON)))
        self.assertEqual(offenders, [])

    def test_manifest_references_existing_files(self):
        manifest = ast.literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))
        missing = [name for name in manifest.get("data", []) if not (ADDON / name).is_file()]
        self.assertEqual(missing, [])
```

- [ ] **Step 2: 运行测试并确认它因现有旧引用失败**

Run: `python3 -m unittest xq_rfid.tests.test_legacy_removal -v`

Expected: `test_runtime_files_do_not_reference_legacy_driver` FAIL，并列出 `models/uhf_reader18_client.py`、旧向导、ACL 等；manifest 文件存在性测试 PASS。

- [ ] **Step 3: 给历史 selection 增加不可操作的过渡值和迁移标记**

在 `RfidDeviceConfig` 中保留可读取历史数据但不可作为新 SI120X1 使用的值：

```python
_check_company_auto = True

company_id = fields.Many2one(
    "res.company",
    required=True,
    default=lambda self: self.env.company,
    index=True,
)
device_type = fields.Selection(
    [
        ("simulation", "模拟设备"),
        ("legacy_disabled", "旧设备（需要重新配置）"),
        ("si120x1", "SI120X1"),
        ("custom", "自定义设备"),
    ],
    default="simulation",
    required=True,
)
migration_required = fields.Boolean(
    readonly=True,
    compute="_compute_migration_required",
    store=True,
)

@api.depends("device_type")
def _compute_migration_required(self):
    for device in self:
        device.migration_required = device.device_type == "legacy_disabled"
```

不要把 `legacy_disabled` 放入任何可选设备 domain；`create()` 拒绝新建该类型，仅允许迁移 SQL 写入或已有记录读取。

- [ ] **Step 4: 写幂等 pre-migration，并保留旧连接值**

本迁移必须使用参数化 SQL，因为 Odoo pre-migration 运行在新 registry/model schema 就绪之前，不能依赖新字段或新 selection 已加载。先用 `to_regclass('rfid_device_config')` 和 `information_schema.columns` 检查表/列，再只更新旧值：

```python
def migrate(cr, installed_version):
    del installed_version
    cr.execute("SELECT to_regclass('rfid_device_config')")
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = 'rfid_device_config'
           AND column_name IN ('active', 'device_type', 'connection_status', 'error_message')
    """)
    columns = {row[0] for row in cr.fetchall()}
    if {'active', 'device_type'} - columns:
        return
    assignments = ["active = FALSE", "device_type = %s"]
    params = ["legacy_disabled"]
    if 'connection_status' in columns:
        assignments.append("connection_status = %s")
        params.append("error")
    if 'error_message' in columns:
        assignments.append("error_message = %s")
        params.append("旧 UHFReader18 配置已停用；必须按 SI120X1 实机接口重新配置并验证。")
    params.append("uhf_reader18")
    cr.execute(
        f"UPDATE rfid_device_config SET {', '.join(assignments)} WHERE device_type = %s",
        params,
    )
```

`migration_required` 是新字段，pre-stage 不写它；在 Task 3 的 post-init/字段默认策略中，`legacy_disabled` 计算或补写为 True。脚本重复运行时更新 0 行且不影响其他设备。

- [ ] **Step 5: 将 manifest 升级到 `18.0.2.0.0` 并修正 data 列表缩进**

只修改版本和缩进；本任务暂不删除旧向导加载项，删除由 Task 2 与对应测试一起完成。

- [ ] **Step 6: 做不接触数据库的语法验证**

Run: `python3 -m py_compile xq_rfid/migrations/18.0.2.0/pre-disable-uhf-reader18.py xq_rfid/models/rfid_device.py`

Expected: exit code 0，无输出。

- [ ] **Step 7: 在用户确认数据库命令后执行迁移场景测试**

Run only after explicit approval: 先在一次性测试数据库中安装 `18.0.1.0.0` 并建立旧/非旧设备 fixture；再将代码切到本计划版本并执行 `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <migration_test_db> -u xq_rfid --stop-after-init`；用只读 Odoo shell 或专用外部 migration harness 比较升级前后快照。不要把 migration 测试伪装成新 registry 中的普通 `TransactionCase`，因为那不会执行真实版本升级路径。

Expected: 旧设备变为 inactive + `legacy_disabled`，新 registry 重算后 `migration_required=True`；其他设备字段不变；第二次升级结果相同。

- [ ] **Step 8: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/tests xq_rfid/migrations xq_rfid/__manifest__.py xq_rfid/models/rfid_device.py
git commit -m "migrate: disable legacy UHF reader devices"
```

### Task 2: 删除 UHFReader18 专用模型、向导、ACL 和文档

**Files:**
- Delete: `xq_rfid/models/uhf_reader18_client.py`
- Delete: `xq_rfid/wizard/uhf_reader18_wizard.py`
- Delete: `xq_rfid/wizard/uhf_reader18_wizard_views.xml`
- Delete: `xq_rfid/tests/test_uhf_reader18.py`
- Delete: `xq_rfid/fix_work_mode.py`
- Delete: `xq_rfid/UHFReader18_TCP_使用说明.md`
- Modify: `xq_rfid/models/__init__.py:12`
- Modify: `xq_rfid/wizard/__init__.py:4`
- Modify: `xq_rfid/__manifest__.py:79`
- Modify: `xq_rfid/security/ir.model.access.csv:7,9-10`
- Inspect and modify only legacy sections: `xq_rfid/DATABASE_MIGRATION.md`, `xq_rfid/QUICK_FIX.md`, `xq_rfid/UPDATE_2025_10_17.md`, `xq_rfid/WIZARD_FIX_README.md`, `xq_rfid/md.md`, `xq_rfid/使用说明.md`, `xq_rfid/修复说明.md`, `xq_rfid/功能更新说明.md`, `xq_rfid/安装说明.md`
- Inspect before deciding: `xq_rfid/csharp_sdk/packages-microsoft-prod.deb`

**Interfaces:**
- Consumes: Task 1 的静态禁止列表。
- Produces: 无旧 Python import、XML data、ACL model ID、向导入口或危险操作 UI。

- [ ] **Step 1: 增加 manifest 与 ACL 的精确断言**

在 `test_legacy_removal.py` 增加：

```python
    def test_manifest_does_not_load_legacy_wizard(self):
        manifest = ast.literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertNotIn("wizard/uhf_reader18_wizard_views.xml", manifest["data"])

    def test_acl_does_not_reference_deleted_models(self):
        acl = (ADDON / "security/ir.model.access.csv").read_text(encoding="utf-8")
        self.assertNotIn("model_uhf_reader18_service", acl)
        self.assertNotIn("model_uhf_reader18_config_wizard", acl)
        self.assertNotIn("model_uhf_reader18_demo_wizard", acl)
```

- [ ] **Step 2: 运行并确认三个删除断言失败**

Run: `python3 -m unittest xq_rfid.tests.test_legacy_removal -v`

Expected: runtime reference、manifest 旧视图和 ACL 旧 model ID 相关测试 FAIL。

- [ ] **Step 3: 删除专用文件及所有加载项**

删除列出的旧专用文件；从两个 `__init__.py`、manifest 和 ACL 中移除对应行。不要保留 Kill、写 EPC、功率或旧工作模式按钮作为“临时诊断工具”。

- [ ] **Step 4: 审阅历史说明与二进制包后最小清理**

对每个说明文件只删除或重写仍指导用户调用旧服务的章节；可保留明确标记为历史且不属于运行文档的变更记录。读取 `packages-microsoft-prod.deb` 的 tracked status、文件类型和附近 README：若只为旧 C# 演示服务，移除并在 Adapter README 说明“不分发厂商或系统安装包”；若有已证明的其他模块消费者，则保持不动并在本任务记录原因。

- [ ] **Step 5: 运行静态回归**

Run: `python3 -m unittest xq_rfid.tests.test_legacy_removal -v`

Expected: manifest/ACL tests PASS；runtime 引用测试仍可能只因 Task 3 尚未重写的 `quality_check.py`、`rfid_device.py`、`quality_point.py`、`rfid_read_wizard.py`、视图而 FAIL，且不得出现已删除文件。

- [ ] **Step 6: 验证 Python import 与 XML 语法**

```bash
python3 -m compileall -q xq_rfid
python3 - <<'PY'
from pathlib import Path
from lxml import etree
for path in Path('xq_rfid').rglob('*.xml'):
    etree.parse(str(path))
print('XML OK')
PY
```

Expected: compile exit code 0；输出 `XML OK`。

- [ ] **Step 7: 建议提交边界（当前不要执行）**

```bash
git add -A xq_rfid
git commit -m "refactor: remove UHFReader18 integration"
```

### Task 3: 让设备、质检点和读取向导在 Adapter 未就绪时 fail-closed

**Files:**
- Modify: `xq_rfid/models/rfid_device.py`
- Modify: `xq_rfid/models/quality_point.py`
- Modify: `xq_rfid/models/quality_check.py`
- Modify: `xq_rfid/wizard/rfid_read_wizard.py`
- Modify: `xq_rfid/views/rfid_device_views.xml`
- Modify: `xq_rfid/wizard/rfid_read_wizard_views.xml`
- Test: `xq_rfid/tests/test_device_fail_closed.py`

**Interfaces:**
- Consumes: Task 1 的 `device_type`, `migration_required`, `company_id`。
- Produces: `_ensure_operational()`；真实硬件入口在 Adapter 客户端尚未出现时统一抛 `UserError`，不调用模拟服务。

- [ ] **Step 1: 写 Odoo TransactionCase 覆盖模拟成功和未验证设备**

```python
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeviceFailClosed(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device = cls.env["rfid.device.config"].create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "company_id": cls.env.company.id,
            "active": True,
        })

    def test_unvalidated_device_is_not_operational(self):
        with self.assertRaisesRegex(UserError, "尚未验证"):
            self.device._ensure_operational()

    def test_abstract_service_never_reports_write_success(self):
        result = self.env["rfid.device.service"].write_rfid_tag({"token": "test"})
        self.assertFalse(result["success"])

    def test_non_manager_cannot_run_hardware_action(self):
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "RFID Non Manager",
            "login": "rfid-non-manager",
            "groups_id": [(6, 0, [self.env.ref("xq_rfid.group_rfid_user").id])],
        })
        with self.assertRaisesRegex(UserError, "管理员"):
            self.device.with_user(user).action_test_connection()
```

- [ ] **Step 2: 在取得数据库确认后运行，并确认旧模拟成功导致失败**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestDeviceFailClosed`

Expected: `test_abstract_service_never_reports_write_success` FAIL；`_ensure_operational` 尚不存在或行为不符。

- [ ] **Step 3: 将抽象服务改为失败，并增加设备统一校验**

```python
def write_rfid_tag(self, data):
    del data
    return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}


def _ensure_operational(self):
    self.ensure_one()
    if not self.active:
        raise UserError(_("RFID 设备已停用。"))
    if self.migration_required:
        raise UserError(_("旧 RFID 设备必须重新配置。"))
    if self.device_type != "si120x1":
        raise UserError(_("该设备不是 SI120X1。"))
    if self.validation_state != "validated":
        raise UserError(_("SI120X1 设备尚未验证。"))
    if self.company_id not in self.env.companies:
        raise UserError(_("无权访问该公司的 RFID 设备。"))
    return True
```

本任务先增加 `validation_state = unvalidated|validated|error`，默认 `unvalidated`；后续 Task 9 由连接测试设置能力字段。

- [ ] **Step 4: 去掉所有旧分派并收窄 domain**

`quality.point.rfid_device_id`、读取向导和默认搜索使用：

```python
[("device_type", "=", "si120x1"),
 ("active", "=", True),
 ("validation_state", "=", "validated"),
 ("company_id", "=", self.env.company.id)]
```

读取向导暂时调用 `device._ensure_operational()` 后抛出“RFID Adapter 尚未配置”；`quality.check` 删除 `_write_to_uhf_reader18()`、`_format_data_for_uhf()` 和旧分支。设备视图删除 `device_address`、旧说明和旧写测试逻辑。

- [ ] **Step 5: 运行静态删除测试，确认运行时旧引用为零**

Run: `python3 -m unittest xq_rfid.tests.test_legacy_removal -v`

Expected: all PASS。

- [ ] **Step 6: 在确认后运行 Odoo fail-closed 测试**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestDeviceFailClosed`

Expected: all PASS。

- [ ] **Step 7: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/models xq_rfid/wizard xq_rfid/views xq_rfid/tests
git commit -m "fix: fail closed without RFID adapter"
```

### Task 4: 加入多公司规则和旧实现移除发布门槛

**Files:**
- Modify: `xq_rfid/security/security.xml`
- Modify: `xq_rfid/views/rfid_device_views.xml`
- Modify: `xq_rfid/views/quality_point_views.xml`
- Test: `xq_rfid/tests/test_device_security.py`
- Modify: `xq_rfid/README.md` if present, otherwise Create: `xq_rfid/README.md`

**Interfaces:**
- Consumes: `rfid.device.config.company_id` 与 RFID 用户/管理员组。
- Produces: `rfid_device_company_rule`；阶段 A 发布检查表。

- [ ] **Step 1: 写跨公司访问失败测试**

```python
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRfidDeviceSecurity(TransactionCase):
    def test_user_cannot_search_other_company_device(self):
        other = self.env["res.company"].create({"name": "RFID Other"})
        device = self.env["rfid.device.config"].sudo().create({
            "name": "Other device",
            "device_type": "si120x1",
            "company_id": other.id,
        })
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "RFID User",
            "login": "rfid-security-user",
            "company_id": self.env.company.id,
            "company_ids": [(6, 0, [self.env.company.id])],
            "groups_id": [(6, 0, [self.env.ref("xq_rfid.group_rfid_user").id])],
        })
        visible = self.env["rfid.device.config"].with_user(user).search([("id", "=", device.id)])
        self.assertFalse(visible)
```

- [ ] **Step 2: 经确认运行并看到跨公司记录当前可见**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestRfidDeviceSecurity`

Expected: FAIL because no device company rule exists。

- [ ] **Step 3: 增加 record rule 与视图 company 字段**

```xml
<record id="rfid_device_company_rule" model="ir.rule">
    <field name="name">RFID devices: allowed companies</field>
    <field name="model_id" ref="model_rfid_device_config"/>
    <field name="domain_force">[('company_id', 'in', company_ids)]</field>
    <field name="groups" eval="[(4, ref('xq_rfid.group_rfid_user'))]"/>
</record>
```

在设备表单和列表显示 `company_id`；质检点字段加 `check_company=True`，服务端约束再次校验 point/device 公司一致，不能只依赖 view domain。

- [ ] **Step 4: 写阶段 A 运维说明**

README 明确：升级前备份、旧设备会停用、管理员必须重新配置、未安装 Adapter 时真实写入必然失败、如何运行静态测试；不要给出真实数据库名或口令。

- [ ] **Step 5: 运行阶段 A 静态门槛**

```bash
python3 -m unittest xq_rfid.tests.test_legacy_removal -v
python3 -m compileall -q xq_rfid
```

Expected: all static tests PASS，compile exit code 0。

- [ ] **Step 6: 经确认运行所有阶段 A Odoo 测试**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid`

Expected: no import/XML/ACL errors；legacy migration、fail-closed 和多公司 tests PASS。

- [ ] **Step 7: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/security xq_rfid/views xq_rfid/tests xq_rfid/README.md
git commit -m "security: isolate RFID devices by company"
```

### Task 5: 实现固定 24 字节载荷 codec

**Files:**
- Create: `xq_rfid/models/rfid_payload.py`
- Modify: `xq_rfid/models/__init__.py`
- Create: `xq_rfid/tests/test_rfid_payload.py`

**Interfaces:**
- Produces: `RfidPayloadService.encode(token: UUID|str, flags: int = 0) -> bytes`、`decode(payload: bytes) -> dict`、`PAYLOAD_VERSION = 1`、`PAYLOAD_SIZE = 24`。

- [ ] **Step 1: 写黄金向量、长度和损坏 CRC 测试**

```python
import unittest
from uuid import UUID
from xq_rfid.models.rfid_payload import decode_payload, encode_payload


class TestRfidPayload(unittest.TestCase):
    def test_encode_is_exactly_24_bytes_and_round_trips(self):
        token = UUID("00112233-4455-6677-8899-aabbccddeeff")
        payload = encode_payload(token, flags=3)
        self.assertEqual(len(payload), 24)
        self.assertEqual(payload[:4], b"XQ\x01\x03")
        self.assertEqual(decode_payload(payload)["token"], token)

    def test_corrupt_crc_is_rejected(self):
        payload = bytearray(encode_payload(UUID(int=1)))
        payload[10] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC32"):
            decode_payload(bytes(payload))
```

- [ ] **Step 2: 运行并确认 import 失败**

Run: `python3 -m unittest xq_rfid.tests.test_rfid_payload -v`

Expected: FAIL with `ModuleNotFoundError: xq_rfid.models.rfid_payload`。

- [ ] **Step 3: 实现纯 Python codec**

```python
import struct
import zlib
from uuid import UUID

MAGIC = b"XQ"
PAYLOAD_VERSION = 1
PAYLOAD_SIZE = 24


def encode_payload(token, flags=0):
    token = token if isinstance(token, UUID) else UUID(str(token))
    if not 0 <= flags <= 0xFF:
        raise ValueError("flags must fit one byte")
    body = MAGIC + bytes((PAYLOAD_VERSION, flags)) + token.bytes
    return body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def decode_payload(payload):
    if len(payload) != PAYLOAD_SIZE:
        raise ValueError("payload must be exactly 24 bytes")
    body, raw_crc = payload[:20], payload[20:]
    if body[:2] != MAGIC or body[2] != PAYLOAD_VERSION:
        raise ValueError("unsupported RFID payload")
    if struct.unpack(">I", raw_crc)[0] != zlib.crc32(body) & 0xFFFFFFFF:
        raise ValueError("invalid RFID payload CRC32")
    return {"version": body[2], "flags": body[3], "token": UUID(bytes=body[4:20])}
```

不要把 codec 建成 ORM 模型；`models/__init__.py` 仅在 Odoo 代码需要模块导入时导入它。

- [ ] **Step 4: 运行测试**

Run: `python3 -m unittest xq_rfid.tests.test_rfid_payload -v`

Expected: 2 tests PASS。

- [ ] **Step 5: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/models/rfid_payload.py xq_rfid/tests/test_rfid_payload.py
git commit -m "feat: add versioned RFID payload codec"
```

### Task 6: 定义 Adapter 领域契约和错误结构

**Files:**
- Create: `services/xq_rfid_adapter/pyproject.toml`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/__init__.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/domain.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/__init__.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/base.py`
- Create: `services/xq_rfid_adapter/tests/__init__.py`
- Create: `services/xq_rfid_adapter/tests/test_domain.py`

**Interfaces:**
- Produces: `AdapterErrorCode`；`AdapterError`；`TagObservation`；`DeviceCapabilities`；`Driver` Protocol；统一结果 envelope `{"ok": bool, "request_id": str|None, "result": dict|None, "error": dict|None}`。

- [ ] **Step 1: 创建无第三方运行依赖的包元数据**

```toml
# services/xq_rfid_adapter/pyproject.toml
[build-system]
requires = ["setuptools>=66"]
build-backend = "setuptools.build_meta"

[project]
name = "xq-rfid-adapter"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: 写错误序列化和 Driver Protocol 测试**

```python
import unittest
from xq_rfid_adapter.domain import AdapterError, AdapterErrorCode


class TestAdapterDomain(unittest.TestCase):
    def test_error_envelope_keeps_safe_device_code(self):
        error = AdapterError(AdapterErrorCode.NO_TAG, "no tag", device_code="0x12")
        self.assertEqual(error.to_dict(), {
            "code": "no_tag",
            "message": "no tag",
            "device_code": "0x12",
            "retryable": False,
        })
```

- [ ] **Step 3: 运行并确认模块不存在**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_domain.py' -v`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 4: 实现精确错误枚举和不可变数据类**

`AdapterErrorCode` 必须包含规格中的 13 类：`configuration_error`、`authentication_error`、`connection_error`、`timeout`、`protocol_error`、`device_error`、`no_tag`、`multiple_tags`、`target_changed`、`unsupported_memory`、`capacity_exceeded`、`write_uncertain`、`verification_failed`。`AdapterError` 的 `safe_message` 不接受 frame/password/payload 字段；retryable 只对 connection/timeout 明确为 True。

`Driver` Protocol 定义：

```python
class Driver(Protocol):
    def test_connection(self) -> dict: ...
    def get_device_info(self) -> dict: ...
    def inventory(self, duration_ms: int, include_tid: bool) -> list[TagObservation]: ...
    def read_memory(self, target: TagTarget, bank: str, word_offset: int, word_count: int) -> bytes: ...
    def write_memory(self, target: TagTarget, bank: str, word_offset: int, payload: bytes) -> dict: ...
    def close(self) -> None: ...
```

`TagTarget` 必须至少有 EPC，能力支持时同时有 TID；bank 第一阶段只允许 `epc|tid|user` 读取和 `user` 写入。

- [ ] **Step 5: 运行测试**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -v`

Expected: domain tests PASS。

- [ ] **Step 6: 建议提交边界（当前不要执行）**

```bash
git add services/xq_rfid_adapter/pyproject.toml services/xq_rfid_adapter/src/xq_rfid_adapter
git commit -m "feat: define RFID adapter contract"
```

### Task 7: 实现 Adapter 配置、HMAC 认证和标准库 HTTP API

**Files:**
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/config.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/api.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/__main__.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/examples/config.example.json`
- Create: `services/xq_rfid_adapter/tests/test_api.py`

**Interfaces:**
- Consumes: Task 6 envelope 与 `AdapterService` 占位 Protocol。
- Produces: `POST /v1/devices/{device_id}/test-connection`、`GET /v1/devices/{device_id}`、`POST /v1/operations`、`GET /v1/operations/{request_id}`；headers `X-RFID-Timestamp`、`X-RFID-Nonce`、`X-RFID-Signature`。

- [ ] **Step 1: 写签名、防重放、body limit 和设备白名单失败测试**

```python
class TestApiAuthentication(unittest.TestCase):
    def test_signature_covers_method_path_timestamp_nonce_and_body(self):
        body = b'{"request_id":"r1"}'
        signature = sign_request(b"secret", "POST", "/v1/operations", "100", "n1", body)
        self.assertTrue(verify_signature(b"secret", signature, "POST", "/v1/operations", "100", "n1", body))

    def test_nonce_cannot_be_reused(self):
        replay = ReplayGuard(ttl_seconds=300)
        replay.accept("n1", now=100)
        with self.assertRaises(AdapterError):
            replay.accept("n1", now=101)
```

HTTP integration test 使用 `ThreadingHTTPServer(("127.0.0.1", 0), handler)`，发送超过 64 KiB body 期望 413；请求未在配置 `devices` 中的 ID 期望 `configuration_error`。

- [ ] **Step 2: 运行并确认签名函数不存在**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_api.py' -v`

Expected: FAIL on missing `config/api` symbols。

- [ ] **Step 3: 实现 HMAC canonical request**

```python
def canonical_request(method, path, timestamp, nonce, body):
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join((method.upper(), path, timestamp, nonce, digest)).encode()


def sign_request(secret, method, path, timestamp, nonce, body):
    return hmac.new(secret, canonical_request(method, path, timestamp, nonce, body), hashlib.sha256).hexdigest()
```

用 `hmac.compare_digest`；timestamp 是 UTC Unix seconds，时间偏差默认不超过 300 秒；nonce 由调用方用 `secrets.token_hex(16)` 生成，并在 SQLite 持久保存到 TTL，不能仅存进程内。actual request-target（含 query string）必须参与签名。响应不得回显签名、密钥、密码或请求 payload。Task 7 与 Task 10 共用至少两个固定 secret/method/path/timestamp/nonce/body/signature 黄金向量，防止两边 canonicalization 漂移。

- [ ] **Step 4: 实现限制明确的标准库服务器**

`config.example.json` 只允许固定 bind 地址、SQLite 路径、`production` 布尔值和设备映射；共享密钥从 `RFID_ADAPTER_SECRET_FILE` 或 `RFID_ADAPTER_SECRET` 读取。生产模式拒绝 `driver="fake"`。跨主机 TLS 由 `ssl.SSLContext` 配置证书；无 TLS 时 bind 必须是 loopback 或 Unix socket。

- [ ] **Step 5: 运行 API 测试**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_api.py' -v`

Expected: signature/replay/body/whitelist tests PASS。

- [ ] **Step 6: 验证 CLI 仅显示帮助，不连接设备**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m xq_rfid_adapter --help`

Expected: 显示 `serve`, `--config`, `--check-config`，exit 0；不得读取或连接 SI120X1。

- [ ] **Step 7: 建议提交边界（当前不要执行）**

```bash
git add services/xq_rfid_adapter/src/xq_rfid_adapter
git commit -m "feat: add authenticated adapter API"
```

### Task 8: 实现 SQLite 幂等 store、设备租约和崩溃恢复

**Files:**
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/store.py`
- Create: `services/xq_rfid_adapter/tests/test_store.py`

**Interfaces:**
- Produces: `OperationStore.create_or_get(request)`、`claim_next(device_id, owner_id, lease_seconds)`、`transition(request_id, expected_state, new_state, result=None, error=None)`、`recover_expired_claims(now)`、`get(request_id)`；DB 唯一键 `request_id`。

- [ ] **Step 1: 写重复 ID、非法状态迁移、租约和重启测试**

```python
class TestOperationStore(unittest.TestCase):
    def test_duplicate_request_id_returns_existing_operation(self):
        first = self.store.create_or_get(sample_request("r1"))
        second = self.store.create_or_get(sample_request("r1"))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.count(), 1)

    def test_expired_claim_is_recovered_without_blind_rewrite(self):
        self.store.create_or_get(sample_request("r2"))
        self.store.claim_next("reader-1", "worker-a", lease_seconds=1, now=100)
        self.store.close()
        reopened = OperationStore(self.path)
        recovered = reopened.recover_expired_claims(now=102)
        self.assertEqual(recovered[0]["state"], "failed_retryable")
        self.assertEqual(recovered[0]["error_code"], "write_uncertain")
```

- [ ] **Step 2: 运行并确认 store 不存在**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_store.py' -v`

Expected: FAIL on missing `OperationStore`。

- [ ] **Step 3: 建表并实现 compare-and-set 状态迁移**

SQLite 使用 WAL、foreign keys、busy timeout；operations 保存 request JSON 的安全字段、state、attempts、claim_owner、lease_until、device_code、safe_error、verified payload hash。允许迁移：

```text
queued -> claimed -> inventorying -> writing -> verifying -> succeeded
claimed/inventorying -> failed_retryable|failed_manual|cancelled
writing/verifying -> succeeded|failed_manual
```

进程重启看到 `writing|verifying` 的过期 claim 时必须进入 `failed_retryable` + `write_uncertain`，由 service 先读回再决定，不能回到 queued 直接写。

- [ ] **Step 4: 实现每设备数据库租约**

`device_leases(device_id PRIMARY KEY, owner_id, lease_until)` 用 `BEGIN IMMEDIATE` + 条件 UPSERT；未过期租约不能被另一 owner 领取。worker 在每个硬件阶段前续租。

- [ ] **Step 5: 运行 store 测试两次**

```bash
PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_store.py' -v
PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_store.py' -v
```

Expected: 两次均 PASS，无临时数据库残留。

- [ ] **Step 6: 建议提交边界（当前不要执行）**

```bash
git add services/xq_rfid_adapter/src/xq_rfid_adapter/store.py services/xq_rfid_adapter/tests/test_store.py
git commit -m "feat: persist idempotent RFID operations"
```

### Task 9: 实现 fake driver 和安全写后验证状态机

**Files:**
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/fake.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/service.py`
- Create: `services/xq_rfid_adapter/src/xq_rfid_adapter/queue.py`
- Create: `services/xq_rfid_adapter/tests/test_service.py`
- Create: `services/xq_rfid_adapter/tests/test_queue.py`

**Interfaces:**
- Consumes: Task 5 payload bytes、Task 6 Driver、Task 8 store。
- Produces: `AdapterService.submit_write_and_verify(...)`、`process_operation(request_id)`、`recover_uncertain(request_id)`；单设备 `DeviceWorker`。

- [ ] **Step 1: 写无标签、多标签、目标变化、验证失败和成功测试**

```python
class TestWriteAndVerify(unittest.TestCase):
    def test_multiple_unique_tags_fail_before_write(self):
        driver = FakeDriver(tags=[tag("E1", "T1"), tag("E2", "T2")])
        result = self.service(driver).process_operation("r1")
        self.assertEqual(result["error_code"], "multiple_tags")
        self.assertEqual(driver.write_calls, 0)

    def test_readback_mismatch_never_succeeds(self):
        driver = FakeDriver(tags=[tag("E1", "T1")], readback=b"wrong" * 5)
        result = self.service(driver).process_operation("r1")
        self.assertEqual(result["state"], "failed_manual")
        self.assertEqual(result["error_code"], "verification_failed")

    def test_same_request_is_written_once(self):
        service = self.service(FakeDriver(tags=[tag("E1", "T1")]))
        service.process_operation("r1")
        service.process_operation("r1")
        self.assertEqual(service.driver.write_calls, 1)
```

- [ ] **Step 2: 运行并确认 service 不存在**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_service.py' -v`

Expected: FAIL on missing service/fake driver。

- [ ] **Step 3: 实现有界流程和目标确认**

流程必须按状态落库：`claimed`→`inventorying`→`writing`→`verifying`。inventory 默认 500 ms，按 `(epc, tid)` 去重；唯一集合必须恰好 1。写入前再次短读目标身份；有 TID 能力时 target 必须同时带 EPC/TID。写 bank 固定 `user`、offset 0、12 words。

- [ ] **Step 4: 实现响应丢失恢复**

`write_memory()` 超时：先转 `verifying` 并 readback；完全相同则 succeeded；完全是已记录旧值且目标相同只允许一次重写；部分变化、无法读取或目标变化转 `failed_manual`。绝不在异常 catch 中无条件再次调用 write。

- [ ] **Step 5: 写并行提交顺序测试**

启动两个线程对同一 `device_id` 提交，fake driver 记录调用区间；断言区间不重叠。对两个不同 device ID 可并行。再创建第二个 store/worker owner，断言租约阻止同时领取同一设备。

- [ ] **Step 6: 运行 service/queue 测试**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_*.py' -v`

Expected: no/multiple/changed/mismatch/idempotency/serialization tests PASS。

- [ ] **Step 7: 将 API 接到 service 和 store**

`POST /v1/operations` 请求固定 schema：

```json
{
  "request_id": "qc-42-write_and_verify-v1",
  "operation_type": "write_and_verify",
  "device_id": "si120x1-line-1",
  "payload_hex": "58510100...",
  "payload_version": 1
}
```

服务端验证 payload 恰好 48 个 hex 字符、解码后 Magic/Version/CRC 有效；重复 request 返回同一 operation。响应只返回状态、脱敏 target、设备状态码和安全错误。

- [ ] **Step 8: 运行全部 Adapter 测试**

Run: `PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -v`

Expected: all PASS。

- [ ] **Step 9: 建议提交边界（当前不要执行）**

```bash
git add services/xq_rfid_adapter/src/xq_rfid_adapter
git commit -m "feat: serialize and verify RFID writes"
```

### Task 10: 实现 Odoo Adapter 客户端和设备能力验证

**Files:**
- Create: `xq_rfid/models/rfid_adapter_client.py`
- Modify: `xq_rfid/models/__init__.py`
- Modify: `xq_rfid/models/rfid_device.py`
- Modify: `xq_rfid/views/rfid_device_views.xml`
- Create: `xq_rfid/tests/test_adapter_client.py`
- Create: `xq_rfid/tests/test_rfid_device.py`

**Interfaces:**
- Consumes: Task 7 API；固定 `adapter_device_id`。
- Produces: `rfid.adapter.client.test_connection(device)`、`get_device_info(device)`、`submit_operation(operation)`、`get_operation(request_id)`；设备 capability 字段。

- [ ] **Step 1: 写 URL 不可由调用方覆盖和错误映射测试**

```python
@tagged("post_install", "-at_install")
class TestAdapterClient(TransactionCase):
    def test_client_builds_url_from_system_configuration_only(self):
        client = self.env["rfid.adapter.client"]
        with self.assertRaises(TypeError):
            client.get_operation("r1", base_url="http://attacker/")

    def test_timeout_maps_to_retryable_error(self):
        with patch("odoo.addons.xq_rfid.models.rfid_adapter_client.requests.request", side_effect=requests.Timeout):
            with self.assertRaisesRegex(UserError, "超时"):
                self.client.get_operation("r1")
```

- [ ] **Step 2: 经确认运行并看到 client 不存在**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestAdapterClient`

Expected: FAIL because model is missing。

- [ ] **Step 3: 实现唯一 HTTP client**

`rfid.adapter.client` 是 `AbstractModel`；base URL、TLS CA、client cert 和 secret file 只从环境变量或仅系统管理员可读的 `ir.config_parameter` 获取。方法签名不接受 URL/IP/port。使用 `requests.request(timeout=(2, 10))`；签名与 Task 7 canonical request 一致；将非 2xx、invalid JSON、Adapter error 分为安全 `UserError`/内部重试错误。公开设备/质检/向导方法必须在调用 client 前显式执行 RFID manager 或对应业务组检查；测试须证明普通 RFID user 无法借 `call_kw` 调用诊断或写入入口。

- [ ] **Step 4: 增加 SI120X1 设备字段**

字段固定为：`adapter_device_id`（required for SI120X1, indexed）、`protocol_family`（unconfirmed/moduleapi_http/moduleapi_sdk/ex10_raw）、`transport_type`（http/tcp_transparent/serial/sdk_tcp/sdk_serial）、`firmware_version`、`hardware_version`、`module_version`、`antenna_count`、`region`、`supports_epc`、`supports_tid`、`supports_user_read`、`supports_user_write`、`last_connection_test_at`、`last_successful_operation_at`、`last_device_code`、`validation_state`。

对 `(company_id, adapter_device_id)` 建唯一约束；普通用户只读主机类字段，管理员才可维护。业务客户端仍只发送 `adapter_device_id`。

- [ ] **Step 5: 连接测试只读取无状态信息并验证能力**

`action_test_connection()` 顺序固定：`_ensure_rfid_manager()`→Adapter `test_connection`→`get_device_info`→保存版本与能力。只有 device model 为 SI120X1、已确定 `protocol_family`、支持 EPC + User read + User write，且 Adapter 返回的 device ID 匹配时才设置 `validated`；否则 `error`，不得尝试写标签。

- [ ] **Step 6: 经确认运行 client/device tests**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags '/xq_rfid:TestAdapterClient,/xq_rfid:TestRfidDevice'`

Expected: all PASS。

- [ ] **Step 7: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/models xq_rfid/views xq_rfid/tests
git commit -m "feat: connect Odoo to RFID adapter"
```

### Task 11: 建立 `rfid.operation`、标签物理身份和安全权限

**Files:**
- Create: `xq_rfid/models/rfid_operation.py`
- Modify: `xq_rfid/models/__init__.py`
- Modify: `xq_rfid/models/rfid_tag.py`
- Modify: `xq_rfid/security/ir.model.access.csv`
- Modify: `xq_rfid/security/security.xml`
- Modify: `xq_rfid/views/rfid_tag_views.xml`
- Create: `xq_rfid/views/rfid_operation_views.xml`
- Modify: `xq_rfid/__manifest__.py`
- Create: `xq_rfid/tests/test_rfid_operation.py`

**Interfaces:**
- Consumes: Task 5 codec、Task 10 client/device。
- Produces: `rfid.operation.create_or_get_for_quality_check(check)`、`action_submit()`、`action_sync()`；`rfid.tag` 物理字段。

- [ ] **Step 1: 写稳定 request ID 和唯一约束测试**

```python
@tagged("post_install", "-at_install")
class TestRfidOperation(TransactionCase):
    def test_request_id_is_stable_for_same_check_and_payload_version(self):
        first = self.env["rfid.operation"]._make_request_id(self.check, "write_and_verify", 1)
        second = self.env["rfid.operation"]._make_request_id(self.check, "write_and_verify", 1)
        self.assertEqual(first, second)
        self.assertEqual(first, f"qc-{self.check.id}-write_and_verify-v1")

    def test_create_or_get_reuses_existing_operation(self):
        first = self.env["rfid.operation"].create_or_get_for_quality_check(self.check)
        second = self.env["rfid.operation"].create_or_get_for_quality_check(self.check)
        self.assertEqual(first, second)
```

- [ ] **Step 2: 经确认运行并看到模型不存在**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestRfidOperation`

Expected: FAIL on missing model。

- [ ] **Step 3: 增加并发唯一冲突 savepoint 测试**

用两个独立 cursor/environment 同时尝试同一 request ID；一个 insert 获胜，另一个只在 savepoint 内收到 `IntegrityError`，随后在可用主事务中重新 search；最终数据库只有一条 operation，两个调用返回同一 ID。

- [ ] **Step 4: 实现操作模型和合法状态迁移**

字段包含 `request_id` unique/index、company/device/check/lot/tag（均 `check_company=True`）、operation_type、payload_version、token UUID string、state、masked_epc/tid、identity_hash、adapter_device_code、safe_error、attempt_count、submitted_at、`submission_notified_at`、finished_at、verification_ok。Token 第一次创建后不可修改；普通用户不得 create/write/unlink operation，RFID manager 可读和发起明确补救操作但不能伪造 succeeded。

`create_or_get_for_quality_check()` 先校验 check/device/company/lot/tag，再按 request_id search；并发唯一冲突必须让 `IntegrityError` 发生在显式 savepoint 内，退出 savepoint 后重新 search，不能在已 aborted 的主事务中继续，也不能捕获没有 savepoint 的数据库异常。

- [ ] **Step 5: 扩展 `rfid.tag` 物理字段**

增加 `epc_hex`、`tid_hex`、`payload_token`、`payload_version`、`last_verified_at`、`written_device_id`、`last_successful_operation_id`。EPC/TID 规范化为大写偶数长度 hex；非空值用 PostgreSQL partial unique indexes 在 company 范围唯一（migration SQL 或 `_auto_init` 明确创建），空值可重复；Token 全局唯一。

- [ ] **Step 6: 增加 ACL、record rule、菜单和只读视图**

`rfid.operation` user: read only；manager: read/create，write 仅通过显式模型方法控制（ACL 可 write，但 override 拒绝敏感字段直接修改），unlink false。company rule 为 `[('company_id', 'in', company_ids)]`。视图不显示完整 payload/access password，只显示 request ID、状态、脱敏身份和安全错误。新增“设备诊断”组（隐含 RFID manager）并将连接测试/读 Bank 入口限制给该组；业务写入只允许由有质检权限的标准流程调用，不能暴露接受任意 payload 的公开模型方法。

- [ ] **Step 7: 经确认运行模型与安全 tests**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags '/xq_rfid:TestRfidOperation,/xq_rfid:TestRfidDeviceSecurity'`

Expected: stable ID/idempotency/constraints/company access PASS。

- [ ] **Step 8: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/models xq_rfid/security xq_rfid/views xq_rfid/tests xq_rfid/__manifest__.py
git commit -m "feat: track idempotent RFID operations"
```

### Task 12: 将 Odoo 18 质检改为异步提交和幂等后台完成

**Files:**
- Modify: `xq_rfid/models/quality_check.py`
- Modify: `xq_rfid/models/quality_point.py`
- Create: `xq_rfid/data/rfid_operation_cron.xml`
- Modify: `xq_rfid/__manifest__.py`
- Create: `xq_rfid/tests/test_quality_rfid_flow.py`

**Interfaces:**
- Consumes: `rfid.operation.create_or_get_for_quality_check()`、`action_submit()`、`action_sync()`。
- Produces: context key `xq_rfid_complete_operation_id`；`quality.check._complete_rfid_operation(operation)`；cron `_cron_sync_adapter_results(limit=20)`。

- [ ] **Step 1: 写缺批次、首次不通过和稳定重放测试**

```python
@tagged("post_install", "-at_install")
class TestQualityRfidFlow(TransactionCase):
    def test_missing_finished_lot_does_not_create_operation_or_pass(self):
        with self.assertRaisesRegex(UserError, "批次"):
            self.check.do_pass()
        self.assertEqual(self.check.quality_state, "none")
        self.assertFalse(self.env["rfid.operation"].search([("quality_check_id", "=", self.check.id)]))

    def test_first_pass_request_queues_operation_but_keeps_check_open(self):
        self.check.do_pass()
        operation = self.env["rfid.operation"].search([("quality_check_id", "=", self.check.id)])
        self.assertEqual(operation.state, "queued")
        self.assertNotEqual(self.check.quality_state, "pass")

    def test_repeated_pass_request_reuses_operation(self):
        self.check.do_pass()
        self.check.do_pass()
        self.assertEqual(self.env["rfid.operation"].search_count([("quality_check_id", "=", self.check.id)]), 1)
```

- [ ] **Step 2: 经确认运行并看到现有同步写流程失败**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestQualityRfidFlow`

Expected: tests FAIL because current `do_pass()` calls hardware/super synchronously。

- [ ] **Step 3: 重写 `do_pass()`，保留标准继承链 guard**

```python
def do_pass(self):
    internal_operation_id = self.env.context.get("xq_rfid_complete_operation_id")
    if internal_operation_id:
        return super().do_pass()

    result = None
    for check in self:
        if check.test_type != "rfid_write":
            result = super(QualityCheck, check).do_pass()
            continue
        operation = self.env["rfid.operation"].create_or_get_for_quality_check(check)
        operation.action_submit()
        if not operation.submission_notified_at:
            check.message_post(body=_("RFID 写入已提交：%s") % operation.request_id)
            operation.submission_notified_at = fields.Datetime.now()
    return result
```

不要捕获宽泛 Exception 后继续通过。对 recordset 混合类型要逐类处理，不能用 `self.test_type` 假定单记录。

- [ ] **Step 4: 写成功同步、防重入和重复同步测试**

fake client 返回 succeeded + EPC/TID + token + verified flag；第一次 `_complete_rfid_operation()` 后断言 check pass、tag 绑定、MRP 标准 side effect 可观察；第二次调用不新增 operation、不重复 chatter、不改变 completion timestamp。

- [ ] **Step 5: 实现 `_complete_rfid_operation()`**

方法逐项验证：operation 属于 check、state succeeded、verification_ok、token 一致、device/check/tag/lot 同公司、返回 identity hash 匹配。然后在当前独立 cron 事务中写 tag 物理字段与 operation link，最后：

```python
check.with_context(xq_rfid_complete_operation_id=operation.id).do_pass()
```

这必须进入 Odoo 18 `quality.check.do_pass()`→`write(quality_state='pass', user_id, control_date)`→MRP workorder override；不得直接写 `quality_state`。若已经 pass 且 operation/tag 已绑定，直接返回。

- [ ] **Step 6: 实现小批量 cron 同步**

XML interval 1 minute，`active=False` 默认，部署验收后管理员启用。cron 每次 limit 20，只取 `queued|claimed|inventorying|writing|verifying|failed_retryable` 操作；每条使用 savepoint，查询 Adapter 后更新；成功调用 completion；失败保持 check 未通过并记录安全错误。不要在同一失败事务中丢失其他结果。

- [ ] **Step 7: 经确认运行质检 tests**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestQualityRfidFlow`

Expected: missing lot/queued/replay/success/reentry/repeated-sync tests PASS。

- [ ] **Step 8: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/models/quality_check.py xq_rfid/models/quality_point.py xq_rfid/data xq_rfid/tests xq_rfid/__manifest__.py
git commit -m "feat: complete RFID quality checks asynchronously"
```

### Task 13: 更新读取诊断和 OWL 操作状态 UI

**Files:**
- Modify: `xq_rfid/wizard/rfid_read_wizard.py`
- Modify: `xq_rfid/wizard/rfid_read_wizard_views.xml`
- Modify: `xq_rfid/static/src/components/mrp_quality_check_confirmation_dialog.js`
- Modify: `xq_rfid/static/src/components/rfid_write_wizard.js`
- Modify: `xq_rfid/static/src/components/rfid_write_wizard.xml`
- Create: `xq_rfid/static/tests/rfid_write_wizard.test.js`
- Modify: `xq_rfid/__manifest__.py`
- Test: `xq_rfid/tests/test_rfid_read_wizard.py`

**Interfaces:**
- Consumes: Task 10 Adapter client；Task 11 operation；Task 12 async behavior。
- Produces: `quality.check.get_rfid_operation_status()` safe RPC；UI states queued/claimed/inventorying/writing/verifying/succeeded/failed。

- [ ] **Step 1: 写读取向导边界测试**

测试管理员可按 `device_id` 读取 EPC/TID/User，普通用户拒绝；Reserve bank 不出现在 selection；word_count 必须 1..128；EPC 是规范 hex；client mock 断言调用参数只有 adapter device ID/target/bank/offset/count，不含任意 URL。

- [ ] **Step 2: 经确认运行并看到旧向导行为失败**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid:TestRfidReadWizard`

Expected: FAIL because current wizard calls deleted legacy service and exposes Reserve bank。

- [ ] **Step 3: 用 Adapter 契约重写诊断向导**

`action_test_connection()` 委托 device；`action_read_rfid()` 先 `_ensure_rfid_manager()`、device `_ensure_operational()`，再 client `read_memory`。只保存 hex 与解析后的 24-byte XQ payload；不要把任意 UTF-8 当产品序列号。错误日志只含 request/device/error code。

- [ ] **Step 4: 增加安全状态 RPC**

```python
def get_rfid_operation_status(self):
    self.ensure_one()
    self.check_access("read")
    operation = self.env["rfid.operation"].search(
        [("quality_check_id", "=", self.id)], order="id desc", limit=1
    )
    return operation._safe_status_dict() if operation else {"state": "not_started"}
```

返回 request_id、state、safe_error、updated_at；不返回 payload、密码、真实 TID/EPC 全值。

- [ ] **Step 5: 写 OWL 状态映射测试**

测试 `queued|claimed|inventorying|writing|verifying|succeeded|failed_retryable|failed_manual|cancelled` 对应文本和 Bootstrap class；failed 显示 safe error；succeeded 后 reload record。用 fake timers 验证组件打开时每 2 秒 poll，关闭/unmount 后停止；不得继续后台无限请求。

- [ ] **Step 6: 重写组件并清理日志/日期解析**

删除所有生产 `console.log/error`。`RfidWriteWizard` state 增加 operationState/requestId/safeError；用 ORM call 获取状态。日期使用 Odoo datetime parser/format 服务，不拼接 `' UTC'`。模板显示真实状态，不再固定“点击验证后写入”；首次 validate 后对话框可关闭，后台 cron 继续。

- [ ] **Step 7: 运行 JS 静态测试和资源构建检查**

若项目已有 Odoo QUnit runner，运行对应 addon test tag；否则至少运行：

```bash
node --check xq_rfid/static/src/components/mrp_quality_check_confirmation_dialog.js
node --check xq_rfid/static/src/components/rfid_write_wizard.js
```

Expected: syntax exit 0。Odoo web QUnit 需服务/数据库，仍必须先确认后执行。

- [ ] **Step 8: 经确认运行读取向导和完整 Odoo tests**

Run only after approval: `/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid`

Expected: all xq_rfid tests PASS，无 missing asset/template/model error。

- [ ] **Step 9: 建议提交边界（当前不要执行）**

```bash
git add xq_rfid/wizard xq_rfid/static xq_rfid/models xq_rfid/tests xq_rfid/__manifest__.py
git commit -m "feat: show asynchronous RFID operation status"
```

### Task 14: 实机证据门槛与唯一 SI120X1 驱动

**Files:**
- Create: `docs/hardware/si120x1-acceptance.md`
- Create exactly one after evidence: `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/moduleapi_http.py` OR `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/moduleapi_sdk.py` OR `services/xq_rfid_adapter/src/xq_rfid_adapter/drivers/ex10_raw.py`
- Create matching test: `services/xq_rfid_adapter/tests/test_moduleapi_http.py` OR `test_moduleapi_sdk.py` OR `test_ex10_raw.py`
- Modify: `services/xq_rfid_adapter/src/xq_rfid_adapter/config.py`
- Modify: `services/xq_rfid_adapter/src/xq_rfid_adapter/README.md`

**Interfaces:**
- Consumes: Task 6 `Driver`；SI120X1 铭牌/固件/接口证据。
- Produces: 唯一 production driver 和可复核验收记录。

- [ ] **Step 1: 记录只读证据，不执行写入**

验收文档必须填写实际值而不是占位符：铭牌 SI120X1、硬件/模块/固件、认证区域、天线数、连接介质、IP/端口或串口参数、`/moduleapi` 是否存在、Linux SDK 是否识别、EX10 透明承载证据、目标标签 EPC/TID/User Bank 容量、厂商许可结论。探测仅允许连接、版本、设备信息；任何网络或实机命令都要先获用户明确确认。

- [ ] **Step 2: 应用硬门槛决策表**

```text
/moduleapi 的 test/info + bounded inventory/read/write 在 SI120X1 固件通过 -> ModuleAPI HTTP
否则 libModuleAPI.so 在隔离 Adapter 主机识别该设备且 ABI/许可确认 -> ModuleAPI SDK
否则供应商文档或抓包证明设备端口透明承载 EX10 -> EX10 raw
否则 -> 停止；向供应商索取 SI120X1 专用协议/Linux SDK
```

未满足任何一行时，Task 14 保持 blocked；不得创建猜测驱动，不得把 fake driver 用于生产。

- [ ] **Step 3A: 若选择 ModuleAPI HTTP，先写 mock contract tests**

覆盖：`syncinventory` request `{"antennas":[...],"timeout":500,"bank_data_option":{"bank":2,"start_block":0,"block_count":6,"access_password":"00000000"}}`；`readtagbank`；`writetagbank` 的 `bank_data` 长度为 4 的倍数；TagFilter 将 EPC/TID hex 转为二进制 mask；非 2xx、invalid JSON、`err_code != 0`、timeout、重复 request；URL 只由白名单配置构造。所有密码在 fixture 中使用测试值且日志断言不出现。

- [ ] **Step 3B: 若选择 ModuleAPI SDK，先写 ABI 和子进程测试**

测试 ctypes 结构大小/offset/argtypes/restype 对照厂商头文件；mock `.so` 验证 Init→ParamSet→Inventory/GetNextTag→Stop→Close；崩溃测试在 Adapter 子进程发生并映射 `device_error`，不得导致 Odoo/HTTP 主进程崩溃；SDK path 只来自配置；记录二进制 SHA-256/架构/安装路径但不复制进仓库。

- [ ] **Step 3C: 若选择 EX10 raw，先写厂商黄金帧测试**

使用文档或抓包中的固定 request/response bytes 验证 `[0xFF][DataLength][Command][Data][CRC16]`、CRC `0x1021` MSB-first/high-byte-first、两字节状态码、大端字段；覆盖错误头、CRC、command mismatch、split/coalesced frames、bounded inventory、bank 边界。不得复用旧 UHFReader18 CRC 或 Adr 字段。

- [ ] **Step 4: 实现唯一驱动并注册显式 driver 名称**

配置只接受本次证据选定的一个 production driver；设备 `protocol_family` 必须与其相同。不要按连接失败依次尝试其他驱动。实现 Task 6 Protocol；任何底层状态统一映射 Task 6 错误，同时保存原始 device code。

- [ ] **Step 5: 运行选定驱动的纯 mock tests**

Run one matching command:

```bash
PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_moduleapi_http.py' -v
# or
PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_moduleapi_sdk.py' -v
# or
PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -p 'test_ex10_raw.py' -v
```

Expected: selected driver tests PASS；另外两个驱动文件不存在。

- [ ] **Step 6: 经明确实机授权执行分阶段验收**

顺序不可跳过：test connection→get info→500 ms inventory no tag→single tag EPC/TID→multiple tag rejection→User Bank read/capacity→在专用可写测试标签写 24 bytes→readback→重复相同 request→断网/设备重启/Adapter 重启→响应丢失恢复。每一步失败立即停止，不继续危险步骤。

- [ ] **Step 7: 记录实机结果并启用 cron/production driver**

验收文档记录时间、操作者、设备/固件、测试标签、每项结果、安全状态码和回滚。只有全部门槛通过才将 Adapter `production=true`、禁用 fake driver并启用 Odoo cron。

- [ ] **Step 8: 建议提交边界（当前不要执行）**

```bash
git add docs/hardware/si120x1-acceptance.md services/xq_rfid_adapter/src/xq_rfid_adapter
git commit -m "feat: add verified SI120X1 driver"
```

### Task 15: 发布前全链路验证和操作文档

**Files:**
- Modify: `services/xq_rfid_adapter/src/xq_rfid_adapter/README.md`
- Modify: `xq_rfid/README.md`
- Create: `docs/decisions/001-si120x1-adapter-boundary.md`
- Create: `docs/decisions/002-si120x1-driver-selection.md`
- Modify: `docs/hardware/si120x1-acceptance.md`

**Interfaces:**
- Consumes: Tasks 1–14 全部交付物。
- Produces: 可重复部署/恢复/密钥轮换/回滚说明和 ADR。

- [ ] **Step 1: 写 ADR-001 固化独立 Adapter 决策**

记录 Odoo 多 worker、原生崩溃隔离、队列/租约、HTTP/Unix socket、安全认证；替代方案包括“Odoo worker 直接加载 SDK”和“每次请求直接 TCP”，明确拒绝原因和运维后果。

- [ ] **Step 2: 写 ADR-002 固化实机驱动选择**

引用 `si120x1-acceptance.md` 的实际证据，记录选择的唯一协议、另外两种候选为何未选、许可和升级固件后重新验证要求。若 Task 14 blocked，则 ADR 状态为 Proposed，发布也保持 blocked。

- [ ] **Step 3: 完成 Adapter 运维 README**

包含：Python/OS 前提、无第三方 web framework、配置 schema、secret file 权限 0600、SQLite 备份/WAL、systemd unit 示例（非 root、ProtectSystem、NoNewPrivileges）、TLS/mTLS、日志脱敏、健康检查、正常停止、崩溃恢复、驱动升级、SDK 许可、密钥轮换和回滚。命令不能包含真实密钥。

- [ ] **Step 4: 运行不接触数据库/设备的完整测试**

```bash
python3 -m unittest xq_rfid.tests.test_legacy_removal xq_rfid.tests.test_rfid_payload -v
PYTHONPATH=services/xq_rfid_adapter/src python3 -m unittest discover -s services/xq_rfid_adapter/tests -v
python3 -m compileall -q xq_rfid
node --check xq_rfid/static/src/components/mrp_quality_check_confirmation_dialog.js
node --check xq_rfid/static/src/components/rfid_write_wizard.js
git diff --check
```

Expected: all tests PASS；compile/node/diff checks exit 0。

- [ ] **Step 5: 扫描旧引用与敏感日志模式**

```bash
python3 - <<'PY'
from pathlib import Path
roots = [Path('xq_rfid/models'), Path('xq_rfid/wizard'), Path('xq_rfid/views'), Path('xq_rfid/security'), Path('xq_rfid/static')]
legacy = ('UHFReader18', 'uhf_reader18', 'uhf.reader18')
sensitive = ('kill_password', 'access_password=%', 'raw_frame')
for root in roots:
    for path in root.rglob('*'):
        if path.is_file():
            text = path.read_text(encoding='utf-8', errors='ignore')
            assert not any(token in text for token in legacy), path
            assert not any(token in text for token in sensitive), path
print('static safety scan OK')
PY
```

Expected: `static safety scan OK`。对 SDK driver 中合法的 access password 变量名，使用专门 logger redaction test，而不是通过删词绕过扫描。

- [ ] **Step 6: 经确认运行模块升级和完整 Odoo tests**

Run only after explicit database approval:

```bash
/usr/bin/odoo --config /etc/odoo/odoo.conf -d <test_db> -u xq_rfid --stop-after-init --test-enable --test-tags /xq_rfid
```

Expected: module upgrade succeeds；0 failed, 0 errors；旧设备安全停用；无 invalid XML/ACL/model/asset。

- [ ] **Step 7: 经确认运行实机验收（仅 Task 14 已通过）**

按 `docs/hardware/si120x1-acceptance.md` 顺序执行；Expected: connection/info/inventory/read/write/readback/idempotency/restart/multi-tag all PASS。未确认设备/标签/网络窗口时跳过并明确标记“未验证”，不得声称生产完成。

- [ ] **Step 8: 检查工作区边界，不提交无关修改**

Run: `git status --short && git diff -- xq_rfid docs`

Expected: 只审阅本计划范围；`.codebase-memory/*` 与 `freeform_quant_delivery/` 不加入任何提交。

- [ ] **Step 9: 建议最终提交边界（当前不要执行）**

```bash
git add xq_rfid docs/decisions docs/hardware
git commit -m "docs: document SI120X1 deployment and recovery"
```
