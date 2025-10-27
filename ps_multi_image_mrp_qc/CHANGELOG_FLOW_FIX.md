# 质检流程完整性修复

## 修改日期
2025年10月9日

## 问题描述
确认数量后，质检流程没有继续执行，导致：
- 无法进入下一个质检点
- 如果是最后一个质检点，无法完成整个生产工单
- 流程被中断，需要手动处理

## 修改内容

### 1. 前端对话框 (`mrp_quality_check_confirmation_dialog.js`)

**之前的逻辑**：
```javascript
// 调用 do_pass 后直接关闭对话框
await this.orm.call('quality.check', 'do_pass', [[recordData.id]]);
this.env.services.notification.add('实际数量已确认并同步', { type: 'success' });
this.props.close();  // ❌ 直接关闭，流程中断
```

**修改后**：
```javascript
// 保存数量后调用父类 validate，继续标准流程
await this.orm.write(...);
return super.validate(...arguments);  // ✅ 继续质检流程
```

### 2. 后端 Wizard (`multi_image_wizard.py`)

**之前的逻辑**：
```python
# sum3 类型完成后直接关闭向导
self.current_check_id.do_pass()
return {'type': 'ir.actions.act_window_close'}  # ❌ 直接关闭
```

**修改后**：
```python
# sum3 类型完成后继续到下一个质检点或完成工单
self.current_check_id.do_pass()
return self.action_generate_next_window()  # ✅ 继续流程
```

## 修改后的完整流程

```
输入数量 → 点击确认
    ↓
保存到数据库（skip_actual_qty_sync=true）
    ↓
调用 super.validate()
    ↓
执行 do_pass() → 同步数据
    ↓
action_generate_next_window()
    ↓
    ├─→ 有下一个质检点？打开下一个质检对话框
    └─→ 最后一个质检点？完成整个生产工单 ✅
```

## 影响范围

- ✅ sum3 类型质检点现在能够正确继续流程
- ✅ 不影响其他质检类型
- ✅ 完全兼容现有流程

## 测试要点

1. ✅ 确认数量后，如果有下一个质检点，应该自动打开
2. ✅ 确认数量后，如果是最后一个质检点，应该完成工单
3. ✅ 工单状态应该正确更新为"已完成"
4. ✅ 数据同步应该只执行一次，不重复

## 相关文件

- `ps_multi_image_mrp_qc/static/src/components/mrp_display/mrp_quality_check_confirmation_dialog.js`
- `ps_multi_image_mrp_qc/wizard/multi_image_wizard.py`
- `ps_multi_image_mrp_qc/BUGFIX_ACTUAL_QTY.md`（详细技术文档）


