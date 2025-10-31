# 模块优化记录

## 优化日期
2025-10-30

## 优化内容

### 1. 清理未使用的代码 ✅

#### 删除的imports和常量：
- ❌ `import operator as py_operator`
- ❌ `from odoo.tools.float_utils import float_round`
- ❌ `OPERATORS` 常量字典

**原因**：这些代码在删除第二单位系统后不再使用。

---

### 2. 删除第二单位系统 ✅

#### 删除的字段：

**产品模板 (product.template)**：
- ❌ `secondary_uom` - 是否开启第二单位
- ❌ `secondary_uom_id` - 第二单位
- ❌ `secondary_uom_ids` - 第二单位选项
- ❌ `secondary_uom_name` - 第二单位名称
- ❌ `secondary_product_qty` - 第二单位在手数量

**库存数量 (stock.quant)**：
- ❌ `secondary_quantity` - 第二单位数量
- ❌ `secondary_uom_id` - 第二单位

**销售订单行 (sale.order.line)**：
- ❌ `secondary_quantity` - 第二单位数量
- ❌ `secondary_uom_id` - 第二单位

**采购订单行 (purchase.order.line)**：
- ❌ `secondary_quantity` - 第二单位数量
- ❌ `secondary_uom_id` - 第二单位

#### 删除的方法：
- ❌ `_compute_uom_id()` - 计算第二单位选项
- ❌ `_compute_secondary_quantities()` - 计算第二单位数量
- ❌ `_compute_secondary_quantities_dict()` - 计算第二单位数量字典
- ❌ `_search_secondary_qty_available()` - 搜索第二单位数量
- ❌ `_compute_secondary_quantity()` (stock.quant)
- ❌ `_compute_secondary_uom_id()` (stock.quant)
- ❌ `_compute_secondary_quantity()` (sale.order.line)
- ❌ `_compute_secondary_quantity()` (purchase.order.line)

#### 删除的视图：
- ❌ `views/sale_order_line_views.xml`
- ❌ `views/purchase_order_line_views.xml`

#### 清理的模型文件：
- ✅ `models/sale_order_line.py` - 仅保留基本继承结构
- ✅ `models/purchase_order_line.py` - 仅保留基本继承结构

#### 更新的视图：
- ✅ `views/product_template_views.xml` - 删除第二单位配置和显示
- ✅ `views/stock_quant_views.xml` - 之前已删除

**原因**：
1. 第二单位系统基于Odoo原生UoM，与附加单位系统功能重复
2. 附加单位系统更符合实际业务需求（手动输入卷数、桶数等）
3. 减少代码复杂度，提高维护性

---

### 3. 统一术语 ✅

#### 术语标准化：

**统一使用"附加单位"**：
- ✅ 字段标签统一为"附加单位"
- ✅ 视图显示统一使用"附加单位"
- ✅ 帮助文本统一表述

**字段命名一致性**：
- `lot_quantity` → "附加单位数量"
- `lot_unit_name` → "附加单位类型"
- `lot_unit_display` → "附加单位"（格式化显示）
- `total_lot_quantity` → "附加单位在手"

---

## 优化效果

### 代码精简：
- 删除代码行数：~200 行
- 删除字段数量：12 个
- 删除方法数量：8 个
- 删除文件数量：2 个

### 性能提升：
- ✅ 减少计算字段依赖
- ✅ 减少数据库查询
- ✅ 简化视图渲染

### 维护性提升：
- ✅ 单一单位系统，避免混淆
- ✅ 代码结构更清晰
- ✅ 减少潜在bug来源

---

## 保留的核心功能

### 附加单位系统：
- ✅ 产品附加单位配置（卷、桶、箱、袋、kg、㎡、自定义）
- ✅ 库存移动行单位记录
- ✅ 库存数量单位汇总
- ✅ 产品看板单位显示

### 产品属性：
- ✅ 产品尺寸（宽度、长度、厚度）
- ✅ 材料属性（密度、固含、粘度）
- ✅ 计算属性（面积、体积）
- ✅ 单位重量（手动填写，kg/m²）

### 库存管理：
- ✅ 批次单位信息
- ✅ 单位数量统计
- ✅ 安全库存预警
- ✅ 备注统计

---

## 迁移影响

### ⚠️ 数据影响：
第二单位相关字段的数据仍保留在数据库中，但不再在UI中显示或计算。

### ✅ 无影响功能：
- 附加单位系统完全独立，不受影响
- 产品属性和库存统计正常工作
- 安全库存功能正常

### 📝 建议：
如果确认不再需要第二单位数据，可以在未来通过数据库迁移脚本清理相关列。

---

## 后续优化建议

### 短期（已完成）：
- ✅ 清理未使用代码
- ✅ 统一术语
- ✅ 删除第二单位系统

### 中期（可选）：
- [ ] 添加字段索引以提升查询性能
- [ ] 优化计算字段的store策略
- [ ] 添加字段验证和约束
- [ ] 完善错误提示信息

### 长期（规划中）：
- [ ] 支持多级附加单位（箱→盒→件）
- [ ] 附加单位转换规则配置
- [ ] 附加单位使用情况报表
- [ ] 移动端单位输入优化

---

## 测试检查清单

### ✅ 功能测试：
- [x] 产品创建和编辑
- [x] 附加单位配置
- [x] 库存移动单位记录
- [x] 产品看板显示
- [x] 库存数量汇总
- [x] 安全库存预警

### ✅ 视图测试：
- [x] 产品表单视图
- [x] 产品看板视图
- [x] 库存数量列表
- [x] 库存移动明细

### ✅ 升级测试：
- [x] 模块升级成功
- [x] 无SQL错误
- [x] 无Python错误
- [x] 视图正常加载

---

## 总结

通过此次优化，`stock_unit_mgmt` 模块变得更加精简、高效和易于维护。
删除了冗余的第二单位系统，专注于核心的附加单位管理功能，为后续功能扩展奠定了良好基础。

**优化前代码行数**：~600 行
**优化后代码行数**：~400 行
**代码精简率**：~33%

🎉 优化完成！

