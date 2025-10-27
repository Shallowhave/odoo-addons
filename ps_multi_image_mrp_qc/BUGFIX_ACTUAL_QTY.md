# 实际数量确认重复输入问题修复说明

## 问题描述

在生产流程的最后质检点（数量确认），用户需要输入两次数量才能成功更新：
- 第一次输入显示：`更新失败: RPC_ERROR: Odoo Server Error`
- 第二次重新输入数量后才能同步成功

## 问题根源分析

### 原始流程存在的问题：

1. **重复触发同步**：
   - 用户在输入框输入数量时，`onChange` 事件立即调用 `orm.write()` 写入数据库
   - 后端 `quality.check` 模型的 `write()` 方法检测到 `actual_qty` 变更，触发同步逻辑：
     - 调用 `_update_production_actual_qty()` 更新制造订单
     - 调用 `_update_sale_orders_actual_qty()` 更新销售订单
   - 用户点击确认按钮时，调用 `do_pass()` 方法，再次触发相同的同步逻辑

2. **状态不一致**：
   - 作为最后一个质检点，在 `onChange` 时质检流程可能还处于过渡状态
   - 相关的制造订单或销售订单可能还没准备好接收更新
   - 导致第一次写入失败

3. **缓存问题**：
   - 前端组件的 `props.record.data` 缓存状态与数据库状态不一致
   - 第二次输入时，缓存已刷新，所以能成功

## 解决方案

### 核心思路

**延迟写入策略**：用户输入时只更新本地状态，只在点击确认按钮时才写入数据库并触发同步。

### 修改内容

#### 1. 前端组件修改 (`actual_qty_input.js`)

**修改点 1**：`onChange` 方法不再立即写入数据库
```javascript
async onChange(ev) {
    const value = this.parseNumber(ev.target.value);
    
    // 验证输入
    const validation = this.validateActualQty(value);
    this.state.isValid = validation.isValid;
    this.state.errorMessage = validation.errorMessage;
    
    if (!validation.isValid) {
        this.notification.add(validation.errorMessage, { type: 'danger' });
        return;
    }

    // 只更新本地状态和 record 数据，不立即写入数据库
    // 数据将在质检确认时统一写入
    this.state.actualQty = value;
    this.props.record.data.actual_qty = value;
}
```

**修改点 2**：`updateField` 方法同步更新 record 数据
```javascript
async updateField(field, value) {
    // 保持本地状态同步
    this.state.actualQty = value;
    this.props.record.data.actual_qty = value;  // 新增
    
    try {
        await this.orm.write('quality.check', [this.props.record.data.id], {
            [field]: value,
        });
        
        this.notification.add(`实际数量已更新为 ${value}`, { type: 'success' });
        
    } catch (e) {
        this.notification.add(`更新失败: ${String(e)}`, { type: 'danger' });
        throw e; // 重新抛出错误以便调用者处理
    }
}
```

#### 2. 对话框修改 (`mrp_quality_check_confirmation_dialog.js`)

**新增验证逻辑**：在确认按钮点击时处理 sum3 类型的特殊逻辑
```javascript
async validate() {
    const recordData = this.props.record.data;
    
    // 如果是 sum3 类型（实际数量确认），需要先保存数量再验证
    if (recordData.test_type === 'sum3') {
        // 验证数量是否有效
        if (!recordData.actual_qty || recordData.actual_qty <= 0) {
            this.env.services.notification.add('请输入有效的实际生产数量', { type: 'danger' });
            return;
        }
        
        try {
            // 先保存实际数量到数据库，使用上下文标志跳过自动同步
            // 这样可以避免重复触发同步逻辑
            await this.orm.write(
                'quality.check', 
                [recordData.id], 
                { actual_qty: recordData.actual_qty },
                { context: { skip_actual_qty_sync: true } }  // 关键：跳过自动同步
            );
            
            // 保存成功后，调用父类的 validate 方法
            // 这会触发标准的质检流程：调用 do_pass，然后继续到下一个质检点或完成工单
            return super.validate(...arguments);
            
        } catch (e) {
            console.error('保存实际数量失败:', e);
            const errorMsg = e.data?.message || e.message || String(e);
            this.env.services.notification.add(`保存失败: ${errorMsg}`, { type: 'danger' });
            return;
        }
    }
    
    // 其他类型的质检调用原始的验证方法
    return super.validate(...arguments);
}
```

#### 3. 对话框模板修改 (`mrp_quality_check_confirmation_dialog.xml`)

**隐藏默认验证按钮，添加专用确认按钮**：
```xml
<!-- 隐藏 sum3 类型的默认验证按钮 -->
<xpath expr="//button[@t-on-click='validate']" position="attributes">
    <attribute name="t-if">(recordData.test_type !== 'passfail' || recordData.quality_state !== 'none') and recordData.test_type !== 'multipic' and recordData.test_type !== 'sum3'</attribute>
</xpath>

<!-- 为 sum3 类型添加专用的确认按钮 -->
<xpath expr="//button[@t-on-click='validate']" position="after">
    <button t-if="recordData.test_type === 'sum3' and recordData.actual_qty > 0" 
            class="btn btn-primary" 
            t-on-click="validate">
        <i class="fa fa-check me-2"/>确认数量
    </button>
</xpath>
```

#### 4. 后端模型修改 (`multi_image_wizard.py`)

**添加上下文标志控制**：
```python
def write(self, vals):
    # ... 现有代码 ...
    
    # 检查是否有实际数量变更
    qty_changed = 'actual_qty' in vals
    
    # 检查上下文标志，决定是否跳过自动同步
    # 如果设置了 skip_actual_qty_sync，则不在 write 中触发同步
    # 这样可以避免在 do_pass 之前的保存操作触发重复同步
    skip_sync = self.env.context.get('skip_actual_qty_sync', False)
    
    res = super(InheritQualityCheck, self).write(vals)
    
    # 只有在未设置跳过标志时才触发自动同步
    if qty_changed and not skip_sync:
        for check in self.filtered(lambda c: c.test_type == 'sum3' and c.actual_qty):
            try:
                # 更新制造订单实际数量
                check._update_production_actual_qty(check.actual_qty)
                # 同步到销售订单
                check._update_sale_orders_actual_qty(check, check.actual_qty)
            except Exception as e:
                _logger.exception('实际数量同步失败 (on write): %s', e)
    return res
```

## 修复后的流程

### 新的工作流程：

1. **用户输入数量**：
   - 触发 `onInput` 事件：实时验证，更新本地状态
   - 触发 `onChange` 事件：验证通过后，只更新本地状态和 `props.record.data`
   - **不写入数据库**

2. **用户点击"确认数量"按钮**：
   - 调用对话框的 `validate()` 方法
   - 检测到是 `sum3` 类型
   - 先调用 `orm.write()` 保存 `actual_qty`，传入上下文 `{ skip_actual_qty_sync: true }`
   - 后端 `write()` 方法检测到上下文标志，**跳过自动同步**
   - 然后调用 `super.validate()`，触发标准质检流程
   - 标准流程调用 `do_pass()` 方法

3. **后端 `do_pass()` 执行同步**：
   - 更新制造订单数量（product_qty, qty_producing）
   - 同步到销售订单（actual_production_qty, sum3_total_count）
   - 标记质检为通过
   - 调用 `action_generate_next_window()`

4. **继续质检流程**：
   - 如果有下一个质检点 → 打开下一个质检对话框
   - 如果是最后一个质检点 → 完成整个生产工单

## 优势

1. **避免重复同步**：通过上下文标志确保同步逻辑只执行一次
2. **状态一致性**：在正确的时机（质检确认时）进行同步
3. **更好的用户体验**：
   - 用户输入时不会触发后台操作
   - 只在确认时才进行数据库操作
   - 避免了第一次失败的错误提示
4. **与其他质检类型一致**：遵循 Odoo 质检流程的标准模式
5. **完整的流程控制**：
   - 确认数量后能够继续到下一个质检点
   - 如果是最后一个质检点，能够正确完成整个工单
   - 不会中断质检流程

## 测试建议

1. **测试正常流程**：输入数量 → 点击确认 → 验证数据同步成功
2. **测试验证逻辑**：输入无效数量（0或负数）→ 验证是否显示错误提示
3. **测试多个质检点**：
   - 确认 sum3 质检点后能够继续到下一个质检点
   - 确保其他质检类型不受影响
4. **测试同步逻辑**：验证制造订单和销售订单的数量是否正确更新
5. **测试最后一个质检点**：
   - 确认作为最后质检点时不再需要输入两次
   - 确认后能够正确完成整个生产工单
   - 验证工单状态正确更新为"已完成"
6. **测试单次同步**：确保数量同步只执行一次，不会重复更新

## 完整工作流程

### 修复后的完整流程：

```
1. 用户输入数量
   └─> 本地状态更新（无数据库操作）
   └─> 实时验证显示

2. 用户点击"确认数量"按钮
   └─> 验证数量有效性
   └─> 保存到数据库（context: skip_actual_qty_sync=true）
   └─> 调用父类 validate() 方法

3. 父类 validate() 触发标准质检流程
   └─> 调用 quality.check.do_pass()
   
4. do_pass() 执行同步逻辑
   ├─> 更新制造订单数量（product_qty, qty_producing）
   ├─> 同步到销售订单（actual_production_qty）
   └─> 标记质检为通过

5. action_generate_next_window()
   ├─> 如果有下一个质检点 → 打开下一个质检对话框
   └─> 如果是最后一个质检点 → 完成整个生产工单
```

### 关键改进：

1. **前端**：保存数量后调用 `super.validate()`，而不是直接关闭对话框
2. **后端 Wizard**：sum3 类型调用 `action_generate_next_window()`，而不是直接返回关闭动作
3. **结果**：确认数量后能够正确完成整个质检流程

## 兼容性说明

- 修改向后兼容，不影响现有的其他质检类型
- 保留了所有原有的同步逻辑，只是改变了触发时机
- 上下文标志是可选的，默认行为保持不变
- 确保 sum3 类型与其他质检类型行为一致，都能正确继续流程

