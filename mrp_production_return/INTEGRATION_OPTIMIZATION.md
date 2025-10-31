# mrp_production_return 与 stock_unit_mgmt 整合优化方案

## 📋 现状分析

### stock_unit_mgmt 模块
- 管理产品的附加单位（卷、桶、箱等）
- 在库存移动时记录 `lot_quantity`（附加单位数量）和 `lot_unit_name`（单位名称）
- 在库存查询中显示附加单位信息

### mrp_production_return 模块
- 处理制造订单的剩余组件
- **当前问题**：只处理标准数量，**未记录附加单位信息**

## 🎯 优化目标

将附加单位信息整合到剩余组件返回流程中，使返回记录更完整。

## 🔧 优化方案

### 1. 向导行添加附加单位字段

**文件**: `models/mrp_production_return_wizard_line.py`

```python
# 添加字段
lot_unit_name = fields.Selection(
    string='附加单位',
    related='move_id.lot_unit_name',
    readonly=True,
    help='原材料的附加单位（如：卷、桶等）'
)

lot_unit_name_custom = fields.Char(
    string='自定义单位',
    related='move_id.lot_unit_name_custom',
    readonly=True
)

lot_quantity_expected = fields.Float(
    string='计划附加单位数量',
    related='move_id.lot_quantity',
    readonly=True,
    help='原计划的附加单位数量'
)

# 添加计算字段
lot_quantity_remaining = fields.Float(
    string='剩余附加单位数量',
    compute='_compute_lot_quantity_remaining',
    help='未消耗的附加单位数量'
)

return_lot_quantity = fields.Float(
    string='返回附加单位数量',
    help='要返回的附加单位数量（如：10卷、5桶）'
)

@api.depends('expected_qty', 'consumed_qty', 'lot_quantity_expected')
def _compute_lot_quantity_remaining(self):
    """计算剩余附加单位数量"""
    for record in self:
        if record.lot_quantity_expected and record.expected_qty:
            # 按比例计算剩余附加单位数量
            ratio = record.remaining_qty / record.expected_qty
            record.lot_quantity_remaining = record.lot_quantity_expected * ratio
        else:
            record.lot_quantity_remaining = 0.0
```

### 2. 向导视图显示附加单位

**文件**: `views/mrp_production_return_wizard_line_views.xml`

```xml
<record id="view_mrp_production_return_wizard_line_tree" model="ir.ui.view">
    <field name="name">mrp.production.return.wizard.line.tree</field>
    <field name="model">mrp.production.return.wizard.line</field>
    <field name="arch" type="xml">
        <tree editable="bottom">
            <field name="product_id"/>
            <field name="expected_qty"/>
            <field name="consumed_qty"/>
            <field name="remaining_qty"/>
            <field name="return_qty"/>
            
            <!-- 附加单位信息 -->
            <field name="lot_unit_name" invisible="not lot_unit_name"/>
            <field name="lot_unit_name_custom" invisible="lot_unit_name != 'custom'"/>
            <field name="lot_quantity_expected" invisible="not lot_unit_name"/>
            <field name="lot_quantity_remaining" invisible="not lot_unit_name"/>
            <field name="return_lot_quantity" invisible="not lot_unit_name"/>
        </tree>
    </field>
</record>
```

### 3. 创建调拨单时传递附加单位

**文件**: `models/mrp_production_return_wizard.py`

修改 `_process_location_return` 方法：

```python
def _process_location_return(self, history, line):
    """处理位置返回（整合附加单位）"""
    # ... 现有代码 ...
    
    move = self.env['stock.move'].create(move_vals)
    
    # === 新增：传递附加单位信息 ===
    if line.return_lot_quantity and line.lot_unit_name:
        # 创建 move line 时设置附加单位信息
        move_line_vals = {
            'move_id': move.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_id.uom_id.id,
            'quantity': line.return_qty,
            'location_id': source_location.id,
            'location_dest_id': self.target_location_id.id,
            # 附加单位信息
            'lot_quantity': line.return_lot_quantity,
            'lot_unit_name': line.lot_unit_name,
            'lot_unit_name_custom': line.lot_unit_name_custom if line.lot_unit_name == 'custom' else False,
        }
        self.env['stock.move.line'].create(move_line_vals)
    
    # ... 其余代码 ...
```

### 4. 历史记录添加附加单位

**文件**: `models/mrp_production_return_history.py`

```python
# 添加字段
lot_quantity = fields.Float(
    string='返回附加单位数量',
    help='返回的附加单位数量'
)

lot_unit_name = fields.Char(
    string='附加单位名称',
    help='附加单位名称（如：卷、桶等）'
)
```

更新 `action_confirm_return` 方法：

```python
history_vals = {
    'production_id': self.production_id.id,
    'product_id': line.product_id.id,
    'quantity': line.return_qty,
    'lot_quantity': line.return_lot_quantity,  # 新增
    'lot_unit_name': self._get_unit_display_name(line),  # 新增
    # ... 其他字段 ...
}
```

## 📊 优化效果

### 优化前
```
剩余组件返回记录：
- 产品：原膜
- 返回数量：100 kg
- ❌ 无附加单位信息
```

### 优化后
```
剩余组件返回记录：
- 产品：原膜
- 返回数量：100 kg
- ✅ 返回附加单位：10 卷  （完整信息）
```

## 🎯 使用场景

1. **原材料剩余处理**
   - 制造时剩余 50kg 原膜（5卷）
   - 返回时记录：50kg + 5卷
   - 调拨单自动包含附加单位信息

2. **库存追溯**
   - 查看返回历史时可以看到具体返回了多少卷/桶
   - 便于库存盘点和管理

3. **生产分析**
   - 统计剩余原材料时包含附加单位
   - 更准确的成本分析

## ⚠️ 注意事项

1. **兼容性**：确保与现有数据兼容，附加单位信息为可选
2. **验证**：返回的附加单位数量应该小于等于剩余数量
3. **传递**：调拨单确认后，附加单位信息会自动写入目标库位的 stock.quant

## 📝 实施步骤

1. ✅ 修改向导行模型，添加附加单位字段
2. ✅ 更新向导视图，显示附加单位信息
3. ✅ 修改调拨单创建逻辑，传递附加单位
4. ✅ 更新历史记录模型，保存附加单位
5. ✅ 测试完整流程

## 🔄 数据流转

```
制造订单剩余组件
    ↓ (lot_quantity, lot_unit_name)
返回向导
    ↓ (传递附加单位信息)
调拨单 (stock.move)
    ↓ (stock.move.line)
目标库位 (stock.quant)
    ↓ (完整的附加单位记录)
库存查询 ✓
```

