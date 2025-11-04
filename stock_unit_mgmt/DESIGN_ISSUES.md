# stock_unit_mgmt 插件设计问题分析

## 🔴 严重问题

### 1. **计算字段依赖不完整** ⚠️
**位置**: `models/stock_quant.py:49`
```python
@api.depends('lot_id', 'product_id', 'quantity', 'location_id')
def _compute_lot_unit_info(self):
```
**问题**: 
- `lot_quantity` 是存储计算字段（`store=True`），但依赖中没有包含 `stock.move.line` 的字段
- 当移动行数据变化时，计算字段不会自动触发重新计算
- 目前通过 `stock_move._action_done` 手动触发，这不是最佳实践

**建议**: 
- 添加反向依赖或使用 `@api.depends` 扩展依赖关系
- 或者移除 `store=True`，改为实时计算（性能影响）

### 2. **未定义变量错误** 🐛
**位置**: `models/stock_quant.py:128`
```python
elif incoming_with_lot_qty:  # ❌ incoming_with_lot_qty 未定义
```
**问题**: 变量 `incoming_with_lot_qty` 在当前作用域中未定义，会导致运行时错误

**建议**: 修复变量引用或重构逻辑

### 3. **性能问题 - 批量操作效率低** ⚠️
**位置**: `models/stock_move.py:51-53`
```python
for quant in quants_to_recompute:
    quant.invalidate_recordset(['lot_quantity', 'lot_unit_name', 'lot_unit_name_custom'])
    quant._compute_lot_unit_info()
```
**问题**: 
- 逐个调用 `invalidate_recordset` 和 `_compute_lot_unit_info` 效率低
- 在大批量移动时会显著影响性能

**建议**: 
- 使用批量操作：`quants_to_recompute._compute_lot_unit_info()`
- 或者一次性更新相关字段触发计算

## 🟡 中等问题

### 4. **日志记录效率低**
**位置**: `models/stock_quant.py:90, 161, 168`
```python
import logging  # 在循环内部导入
_log = logging.getLogger(__name__)
```
**问题**: 
- 日志导入在条件判断内部，应该在外层导入
- 调试代码硬编码了批次名称 `'ppxx01'`，应该移除或配置化

**建议**: 
- 将日志导入移到文件顶部
- 移除或配置化调试代码

### 5. **数据库查询优化不足**
**位置**: `models/stock_quant.py:65-69`
```python
all_move_lines = self.env['stock.move.line'].search([
    ('lot_id', '=', quant.lot_id.id),
    ('product_id', '=', quant.product_id.id),
    ('state', '=', 'done')
])
```
**问题**: 
- 每个 `stock_quant` 记录都执行一次数据库查询
- 在大批量计算时会产生 N+1 查询问题

**建议**: 
- 对于批量计算，可以预先加载所有移动行
- 使用 `read_group` 或批量查询优化

### 6. **字段命名不一致**
**位置**: `models/product_template.py:87-90`
```python
lot_qty = fields.Float(string='第二单位数量', ...)  # 字段名不够清晰
act_juan = fields.Integer(string='实际卷数', ...)  # 字段名不够清晰
o_note = fields.Char(string='备注卷数', ...)  # 字段名不够清晰
```
**问题**: 
- 字段名使用缩写，可读性差
- `lot_qty` 和 `lot_quantity` 容易混淆

**建议**: 统一命名规范，使用更有意义的字段名

### 7. **使用 `hasattr` 检查字段存在**
**位置**: 多处使用 `hasattr` 检查字段
```python
if hasattr(product_tmpl, 'default_unit_config'):
```
**问题**: 
- Odoo 模型字段应该直接访问，不需要 `hasattr` 检查
- 可能导致代码逻辑混乱

**建议**: 直接访问字段，依赖模块正确安装和依赖关系

## 🟢 轻微问题

### 8. **异常处理过于宽泛**
**位置**: `models/stock_quant.py:166`
```python
except Exception as e:
    # 捕获所有异常
```
**问题**: 捕获所有异常可能隐藏真实问题

**建议**: 捕获特定异常类型，或至少记录更详细的错误信息

### 9. **代码重复**
**位置**: 多处重复的单位映射代码
```python
unit_map = {
    'kg': '公斤',
    'roll': '卷',
    ...
}
```

**建议**: 提取为类常量或辅助方法

### 10. **视图继承优先级**
**问题**: 多个模块可能继承相同的视图，需要确保优先级正确

**建议**: 明确设置视图 `priority` 属性

## 📋 优化建议总结

### 高优先级
1. ✅ 修复 `incoming_with_lot_qty` 未定义变量
2. ✅ 优化 `_action_done` 中的批量计算逻辑
3. ✅ 移除或配置化调试代码
4. ✅ 将日志导入移到文件顶部

### 中优先级
5. ⚠️ 优化计算字段依赖关系
6. ⚠️ 优化数据库查询（批量加载）
7. ⚠️ 统一字段命名规范

### 低优先级
8. 📝 改进异常处理
9. 📝 提取重复代码
10. 📝 优化视图继承

