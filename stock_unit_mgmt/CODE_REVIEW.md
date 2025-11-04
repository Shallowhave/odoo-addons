# stock_unit_mgmt 模块代码审查与优化建议

## 📋 审查概述
**审查日期**: 2025-11-04  
**审查范围**: 完整模块源码  
**审查重点**: 代码质量、设计模式、性能优化、可维护性

---

## 🔴 严重问题（必须修复）

### 1. **计算字段依赖不完整** ⚠️
**位置**: `models/stock_quant.py:61`
```python
@api.depends('lot_id', 'product_id', 'quantity', 'location_id')
def _compute_lot_unit_info(self):
```
**问题**: 
- 存储计算字段（`store=True`）依赖中没有包含 `stock.move.line` 的字段
- 当移动行数据变化时，计算字段不会自动触发重新计算
- 目前通过 `stock_move._action_done` 手动触发，这不是最佳实践

**影响**: 
- 数据可能不一致
- 需要手动触发才能更新

**建议**: 
```python
# 添加反向依赖或使用更精确的依赖
@api.depends('lot_id', 'product_id', 'quantity', 'location_id',
             'lot_id.stock_move_line_ids.lot_quantity',
             'lot_id.stock_move_line_ids.lot_unit_name')
```
或者改为非存储计算字段，实时计算（性能影响需评估）

---

### 2. **N+1 查询问题** ⚠️
**位置**: `models/stock_quant.py:77-81`
```python
all_move_lines = self.env['stock.move.line'].search([
    ('lot_id', '=', quant.lot_id.id),
    ('product_id', '=', quant.product_id.id),
    ('state', '=', 'done')
])
```
**问题**: 
- 在 `_compute_lot_unit_info` 中，每个 `stock_quant` 记录都执行一次数据库查询
- 批量计算时会产生 N+1 查询问题，性能严重下降

**影响**: 
- 大量库存记录时性能极差
- 可能导致超时

**建议**: 
```python
# 批量加载所有移动行
@api.depends(...)
def _compute_lot_unit_info(self):
    # 批量获取所有相关的移动行
    lot_ids = self.mapped('lot_id.id')
    product_ids = self.mapped('product_id.id')
    
    all_move_lines = self.env['stock.move.line'].search([
        ('lot_id', 'in', lot_ids),
        ('product_id', 'in', product_ids),
        ('state', '=', 'done')
    ])
    
    # 按 lot_id 和 product_id 建立索引
    move_lines_by_lot_product = {}
    for ml in all_move_lines:
        key = (ml.lot_id.id, ml.product_id.id)
        if key not in move_lines_by_lot_product:
            move_lines_by_lot_product[key] = []
        move_lines_by_lot_product[key].append(ml)
    
    # 然后在循环中使用索引
    for quant in self:
        key = (quant.lot_id.id, quant.product_id.id)
        move_lines = move_lines_by_lot_product.get(key, [])
        # ... 处理逻辑
```

---

### 3. **硬编码的调试代码** 🐛
**位置**: `models/stock_quant.py:111-121, 177-182`
```python
if quant.lot_id and quant.lot_id.name and 'ppxx01' in quant.lot_id.name:
    import logging
    _log = logging.getLogger(__name__)
    _log.info(...)
```
**问题**: 
- 硬编码了批次名称 `'ppxx01'`，这是临时调试代码
- 应该移除或改为配置化
- `import logging` 在循环内部重复导入（虽然影响很小）

**建议**: 
- 移除硬编码的调试代码
- 使用配置参数控制是否启用详细日志
- 将日志导入移到文件顶部

---

### 4. **重复的单位映射代码** 📝
**位置**: 多处
- `models/product_template.py:258-268`
- `models/stock_move_line.py:179-191`
- `models/stock_quant.py:202-210`
- `wizard/product_unit_setup_wizard.py:492-503`

**问题**: 
- 相同的单位映射字典在多个文件中重复定义
- 维护困难，修改时需要同步多个地方

**建议**: 
```python
# 在 models/__init__.py 或创建一个 utils.py
UNIT_DISPLAY_MAP = {
    'kg': '公斤(kg)',
    'roll': '卷',
    'barrel': '桶',
    'box': '箱',
    'bag': '袋',
    'sqm': '平方米(㎡)',
    'piece': '件',
    'custom': '自定义'
}

def get_unit_display_name(unit_code):
    return UNIT_DISPLAY_MAP.get(unit_code, unit_code)
```

---

## 🟡 中等问题（建议优化）

### 5. **过度的 `hasattr` 检查** ⚠️
**位置**: 多处，如 `models/stock_move_line.py:78, 115, 116, 128`
```python
if not hasattr(product_tmpl, 'default_unit_config') or not product_tmpl.default_unit_config:
```
**问题**: 
- Odoo 模型字段应该直接访问，不需要 `hasattr` 检查
- 如果模块依赖正确，字段肯定存在
- 过度使用 `hasattr` 可能导致代码逻辑混乱

**建议**: 
- 直接访问字段，依赖模块依赖关系
- 如果确实需要兼容性检查，应该在模块级别处理

---

### 6. **计算字段性能问题** ⚠️
**位置**: `models/product_template.py:152-180, 182-208, 215-231`
```python
@api.depends('product_variant_ids.stock_quant_ids', ...)
def _compute_o_note(self):
    quants = self.env['stock.quant'].sudo().search([...])
```
**问题**: 
- 在计算字段中每次都执行 `search` 查询
- 即使已经在 `@api.depends` 中声明了依赖，仍然重新查询
- 应该使用 `mapped` 或直接访问依赖字段

**建议**: 
```python
@api.depends('product_variant_ids.stock_quant_ids.o_note1', 
             'product_variant_ids.stock_quant_ids.o_note2')
def _compute_o_note(self):
    for product in self:
        # 直接使用已加载的 quants
        quants = product.product_variant_ids.mapped('stock_quant_ids')
        quants = quants.filtered(lambda q: q.location_id.usage == 'internal' and q.quantity > 0)
        # ... 处理逻辑
```

---

### 7. **字段命名不一致** 📝
**位置**: 多处
- `lot_qty` vs `lot_quantity`
- `safty_qty` vs `safety_qty` (拼写错误)
- `o_note` vs `o_note1`/`o_note2`

**问题**: 
- 字段命名不一致，容易混淆
- `safty` 是拼写错误，应该是 `safety`

**建议**: 
- 统一使用 `lot_quantity`（已基本统一）
- 修正拼写错误：`safty_qty` → `safety_qty`
- 考虑使用更清晰的命名：`o_note` → `remarks_summary`

---

### 8. **异常处理过于宽泛** ⚠️
**位置**: `models/stock_quant.py:183-195`
```python
except Exception as e:
    _logger.error(...)
    quant.lot_unit_name = False
    quant.lot_quantity = 0.0
```
**问题**: 
- 捕获所有异常可能隐藏真实问题
- 应该捕获特定异常类型

**建议**: 
```python
except (ValueError, TypeError, AttributeError) as e:
    _logger.error(...)
    quant.lot_unit_name = False
    quant.lot_quantity = 0.0
except Exception as e:
    # 对于未知异常，记录详细信息并重新抛出
    _logger.error(..., exc_info=True)
    raise
```

---

### 9. **计算长度字段的单位判断逻辑复杂** 📝
**位置**: `models/stock_quant.py:244-273`
```python
# 获取单位名称（支持多语言和JSON格式）
uom_name = ''
try:
    lang = self.env.context.get('lang', 'zh_CN')
    uom_name = uom_id.with_context(lang=lang).name or ''
    # ... 复杂的处理逻辑
```
**问题**: 
- 单位名称判断逻辑过于复杂
- 应该提取为独立方法
- 可以优化为更简洁的判断

**建议**: 
```python
def _is_sqm_unit(self, uom_id):
    """判断单位是否是平米单位"""
    if not uom_id:
        return False
    
    try:
        lang = self.env.context.get('lang', 'zh_CN')
        uom_name = uom_id.with_context(lang=lang).name or ''
        if not uom_name:
            uom_name = uom_id.name or ''
        
        if isinstance(uom_name, dict):
            uom_name = uom_name.get('zh_CN') or uom_name.get('en_US') or str(uom_name)
        
        uom_name = str(uom_name).lower()
        sqm_keywords = ['平米', '平方米', 'sqm', 'm²', 'm2']
        
        if any(keyword in uom_name for keyword in sqm_keywords):
            return True
        
        # 检查单位类别
        if uom_id.category_id:
            category_name = (uom_id.category_id.name or '').lower()
            if '面积' in category_name or 'area' in category_name:
                return True
        
        return False
    except Exception:
        return False
```

---

### 10. **Wizard 中的硬编码逻辑** 📝
**位置**: `wizard/product_unit_setup_wizard.py:141-148`
```python
if self.is_finished_product:
    self.default_unit_config = 'sqm'
elif self.product_length and self.product_length > 0:
    self.default_unit_config = 'roll'
else:
    self.default_unit_config = 'kg'
```
**问题**: 
- 推荐逻辑硬编码，不够灵活
- 应该可配置

**建议**: 
- 使用配置参数或设置表
- 或者提取为配置方法

---

## 🟢 轻微问题（可选优化）

### 11. **代码重复** 📝
- `_get_unit_display_name` 方法在多个类中重复
- 单位映射字典重复定义

**建议**: 提取为工具函数或基类方法

---

### 12. **日志级别不当** 📝
**位置**: `models/stock_quant.py:298`
```python
_logger.info(f"[计算长度] 产品={product.name}, ...")
```
**问题**: 
- 正常计算过程使用 `INFO` 级别，可能产生过多日志

**建议**: 
- 改为 `DEBUG` 级别
- 或使用配置控制是否记录

---

### 13. **字段注释和帮助文本** 📝
**问题**: 
- 部分字段缺少详细的帮助文本
- 计算字段的计算逻辑应该在帮助文本中说明

**建议**: 
- 补充完整的字段帮助文本
- 在计算字段的帮助文本中说明计算公式

---

### 14. **视图继承优先级** 📝
**位置**: `views/stock_quant_views.xml`
```python
<field eval="99" name="priority"/>
<field eval="1000" name="priority"/>
```
**问题**: 
- 多个视图继承的优先级设置不一致
- 可能导致视图覆盖顺序不确定

**建议**: 
- 统一视图优先级设置规则
- 文档化优先级策略

---

## 📊 性能优化建议

### 15. **批量操作优化**
- `_compute_lot_unit_info`: 批量加载移动行（已在上文说明）
- `_compute_total_lot_quantity`: 使用 `read_group` 聚合
- `_compute_is_safty`: 优化查询，使用 `read_group`

### 16. **数据库索引**
建议添加以下索引：
```sql
-- 优化 stock.move.line 查询
CREATE INDEX idx_move_line_lot_product_state 
ON stock_move_line(lot_id, product_id, state) 
WHERE state = 'done';

-- 优化 stock.quant 查询
CREATE INDEX idx_quant_product_location 
ON stock_quant(product_id, location_id) 
WHERE quantity > 0;
```

---

## 🏗️ 架构设计建议

### 17. **模块化改进**
- 将单位管理逻辑提取为独立的 mixin 类
- 将计算字段逻辑提取为独立的计算方法类

### 18. **数据一致性**
- 添加约束确保 `lot_quantity >= 0`
- 添加约束确保 `lot_unit_name` 和 `lot_unit_name_custom` 的一致性

### 19. **向后兼容性**
- 如果字段重命名，应提供迁移脚本
- 考虑使用 `@api.model_cr` 进行数据迁移

---

## ✅ 已做好的地方

1. ✅ 错误处理：大部分地方都有异常处理
2. ✅ 字段验证：使用了 `@api.constrains` 进行验证
3. ✅ 代码组织：文件结构清晰，模块划分合理
4. ✅ 文档：部分方法有注释说明

---

## 📝 总结

### 优先级排序
1. **高优先级**（必须修复）:
   - 计算字段依赖不完整（问题1）
   - N+1 查询问题（问题2）
   - 移除硬编码调试代码（问题3）

2. **中优先级**（建议优化）:
   - 减少 `hasattr` 使用（问题5）
   - 优化计算字段性能（问题6）
   - 统一字段命名（问题7）

3. **低优先级**（可选优化）:
   - 代码重复提取（问题11）
   - 日志级别调整（问题12）
   - 字段帮助文本补充（问题13）

---

**审查完成日期**: 2025-11-04  
**建议实施时间**: 根据优先级逐步实施
