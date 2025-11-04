# stock_unit_mgmt 模块优化建议

## 📊 对比分析：当前实现 vs Odoo原生 vs 企业需求

### 一、核心功能对比

#### 1. **单位管理方式**

**Odoo原生方式**：
- 使用 `uom.uom`（计量单位）和 `uom.category`（单位类别）
- 通过 `factor` 和 `uom_type` 实现单位转换
- 支持多单位（采购单位、销售单位、库存单位）
- 单位转换是自动的、标准化的

**当前实现**：
- 使用自定义的"附加单位"系统（`lot_unit_name`, `lot_quantity`）
- 附加单位与Odoo标准单位系统分离
- 手动输入单位数量，不自动转换

**企业需求**：
- 原膜：以"卷"为单位，需要记录每卷的平米数、重量
- 成品膜：以"平米"为单位，需要记录每平方米的重量
- 液体：以"桶"为单位，需要记录每桶的重量、体积
- 半成品原膜：类似原膜

**优化建议**：
1. **统一单位管理**：将附加单位与Odoo标准UoM系统整合
2. **自动转换机制**：基于产品属性（宽度、长度、密度）自动计算单位转换比例
3. **多单位支持**：为同一产品支持多个单位（卷、平米、吨、桶等），自动转换

---

### 二、数据一致性问题

#### 问题1：附加单位与标准单位不同步

**当前状况**：
- 标准单位（`uom_id`）：可能是"平米"、"卷"、"公斤"
- 附加单位（`lot_unit_name`）：可能是"卷"、"桶"、"箱"
- 两者可能不一致，导致混乱

**优化建议**：
```python
# 建议：附加单位应该映射到标准UoM
class ProductTemplate(models.Model):
    _inherit = 'product.template'
    
    # 附加单位配置：映射到标准UoM
    additional_unit_ids = fields.Many2many(
        'uom.uom',
        'product_additional_unit_rel',
        string='附加单位',
        help='选择可用的附加单位（必须与主单位在同一类别）'
    )
    
    # 自动计算单位转换比例
    def get_unit_conversion(self, from_unit, to_unit, quantity=1.0):
        """根据产品属性自动计算单位转换"""
        # 基于产品尺寸、密度等计算
        pass
```

---

#### 问题2：计算字段性能

**当前状况**：
- `_compute_lot_unit_info` 已优化批量查询
- 但 `_compute_total_lot_quantity`, `_compute_o_note` 等仍可能性能问题

**优化建议**：
1. 使用 `read_group` 进行聚合计算
2. 添加数据库索引
3. 考虑使用存储计算字段 + 定时任务更新

---

### 三、业务逻辑优化

#### 1. **产品类型识别**

**当前状况**：
- 删除了 `product_type` 字段
- 通过 `is_finished_product` 等计算字段判断
- 逻辑分散，不够清晰

**优化建议**：
```python
# 建议：使用Selection字段明确产品类型
product_category_type = fields.Selection([
    ('raw_film', '原膜'),
    ('finished_film', '成品膜'),
    ('liquid', '液体'),
    ('semi_finished', '半成品原膜'),
    ('other', '其他')
], string='产品类别', default='other')

# 根据产品类别自动配置单位
@api.onchange('product_category_type')
def _onchange_product_category_type(self):
    if self.product_category_type == 'raw_film':
        # 自动配置：主单位=卷，附加单位=平米、吨
        pass
    elif self.product_category_type == 'finished_film':
        # 自动配置：主单位=平米，附加单位=米
        pass
```

---

#### 2. **单位配置向导优化**

**当前状况**：
- 向导可以自动计算单位转换比例
- 但需要手动选择产品类型
- 计算逻辑较复杂

**优化建议**：
1. **自动识别产品类型**：基于产品属性自动判断
2. **批量配置**：支持一次配置多个产品
3. **配置模板**：为不同产品类型提供预设模板

---

#### 3. **库存移动时的单位处理**

**当前状况**：
- 收货时手动输入附加单位数量
- 出库时也需要手动输入
- 容易出现不一致

**优化建议**：
1. **自动计算**：根据标准单位数量自动计算附加单位数量
2. **验证机制**：检查附加单位数量是否合理（基于转换比例）
3. **批量操作**：支持批量设置附加单位

---

### 四、数据模型优化

#### 1. **字段命名规范化**

**当前问题**：
- `safty_qty` → 拼写错误（应为 `safety_qty`）
- `o_note`, `o_note1`, `o_note2` → 命名不够清晰
- `lot_qty` vs `lot_quantity` → 不一致

**优化建议**：
```python
# 统一命名规范
safety_stock_qty = fields.Float(string='安全库存')  # 修正拼写
is_safety_stock = fields.Boolean(string='是否安全库存')  # 修正拼写
remark_note1 = fields.Char(string='备注1')  # 更清晰的命名
remark_note2 = fields.Char(string='备注2')
additional_unit_quantity = fields.Float(string='附加单位数量')  # 统一使用
```

---

#### 2. **数据完整性约束**

**当前状况**：
- 缺少数据库级别的约束
- 依赖应用层验证

**优化建议**：
```python
_sql_constraints = [
    ('lot_quantity_positive', 'CHECK(lot_quantity >= 0)', 
     '附加单位数量不能为负数'),
    ('safety_stock_positive', 'CHECK(safety_stock_qty >= 0)', 
     '安全库存不能为负数'),
    ('product_width_positive', 'CHECK(product_width > 0 OR product_width IS NULL)', 
     '产品宽度必须大于0'),
]
```

---

### 五、用户体验优化

#### 1. **库存移动界面**

**当前状况**：
- 附加单位字段在操作界面中
- 需要手动输入，容易遗漏

**优化建议**：
1. **智能默认值**：根据产品配置自动填充
2. **自动计算**：根据标准单位数量自动计算
3. **验证提示**：输入不合理时给出提示
4. **批量设置**：支持批量设置多个批次的附加单位

---

#### 2. **产品视图优化**

**当前状况**：
- 产品属性分散在多个标签页
- 看板视图显示信息有限

**优化建议**：
1. **统一产品属性页**：将所有产品属性集中显示
2. **智能看板**：根据产品类型显示不同的关键信息
3. **快速操作**：在看板中直接进行常用操作（设置单位、查看库存等）

---

#### 3. **库存列表视图**

**当前状况**：
- 显示附加单位和数量
- 但缺少筛选和分组功能

**优化建议**：
1. **筛选器**：按附加单位类型筛选
2. **分组**：按附加单位分组显示
3. **搜索**：支持按附加单位名称搜索

---

### 六、性能优化建议

#### 1. **数据库索引**

```sql
-- 优化 stock.move.line 查询
CREATE INDEX idx_move_line_lot_product_state 
ON stock_move_line(lot_id, product_id, state) 
WHERE state = 'done';

-- 优化 stock.quant 查询
CREATE INDEX idx_quant_product_location_quantity 
ON stock_quant(product_id, location_id, quantity) 
WHERE quantity > 0;

-- 优化产品查询
CREATE INDEX idx_product_tmpl_width_density 
ON product_template(product_width, finished_density) 
WHERE product_width > 0;
```

---

#### 2. **计算字段优化**

**当前问题**：
- 部分计算字段在每次访问时都重新计算
- 存储计算字段的依赖关系可能不完整

**优化建议**：
1. **使用 `read_group`**：对于聚合计算，使用 `read_group` 更高效
2. **批量计算**：在后台任务中批量更新计算字段
3. **缓存机制**：对于不常变化的数据，使用缓存

---

#### 3. **查询优化**

**当前状况**：
- 已优化 N+1 查询问题
- 但仍有改进空间

**优化建议**：
```python
# 使用 read_group 进行聚合
@api.depends(...)
def _compute_total_lot_quantity(self):
    for template in self:
        # 使用 read_group 更高效
        result = self.env['stock.quant'].read_group(
            [
                ('product_tmpl_id', '=', template.id),
                ('location_id.usage', '=', 'internal'),
                ('quantity', '>', 0)
            ],
            ['lot_quantity:sum'],
            []
        )
        template.total_lot_quantity = result[0].get('lot_quantity', 0.0) if result else 0.0
```

---

### 七、集成优化建议

#### 1. **与标准UoM系统集成**

**当前状况**：
- 附加单位系统独立于标准UoM
- 无法利用Odoo的标准单位转换功能

**优化建议**：
- 将附加单位映射到标准UoM
- 使用Odoo的 `uom.factor` 机制进行自动转换
- 在库存移动时自动使用标准单位转换

---

#### 2. **与采购/销售模块集成**

**当前状况**：
- 附加单位主要在库存移动中使用
- 采购和销售中没有使用

**优化建议**：
- 在采购订单中显示附加单位信息
- 在销售订单中显示附加单位信息
- 支持按附加单位采购/销售

---

#### 3. **与报表系统集成**

**当前状况**：
- 缺少基于附加单位的报表

**优化建议**：
1. **库存报表**：按附加单位统计库存
2. **采购报表**：按附加单位统计采购
3. **销售报表**：按附加单位统计销售
4. **库存周转报表**：基于附加单位分析

---

### 八、代码质量改进

#### 1. **减少 hasattr 使用**

**当前状况**：发现 22 处 `hasattr` 使用

**优化建议**：
- 直接访问字段，依赖模块依赖关系
- 如果需要兼容性检查，使用 `@api.model` 装饰器

---

#### 2. **异常处理细化**

**当前状况**：
- 部分地方使用 `except Exception` 过于宽泛

**优化建议**：
```python
# 捕获特定异常
except (ValueError, TypeError, AttributeError) as e:
    _logger.warning(...)
except Exception as e:
    _logger.error(..., exc_info=True)
    raise  # 重新抛出未知异常
```

---

#### 3. **单元测试**

**当前状况**：
- 缺少单元测试

**优化建议**：
- 为关键计算方法添加单元测试
- 测试单位转换逻辑
- 测试数据一致性

---

### 九、企业需求专项优化

#### 1. **原膜管理**
- ✅ 已支持：以"卷"为单位
- ⚠️ 可优化：
  - 自动计算每卷的平米数（基于长度×宽度）
  - 自动计算每卷的重量（基于体积×密度）
  - 批量设置原膜属性

#### 2. **成品膜管理**
- ✅ 已支持：以"平米"为单位
- ⚠️ 可优化：
  - 自动计算每平米的重量（基于厚度×密度）
  - 自动计算发货重量（面积×重量系数）
  - 支持按长度计算（基于面积和宽度）

#### 3. **液体管理**
- ✅ 已支持：以"桶"为单位
- ⚠️ 可优化：
  - 自动计算每桶的重量（基于体积×密度）
  - 支持按重量计算（基于固含量）
  - 支持按体积计算

#### 4. **半成品原膜管理**
- ✅ 已支持：类似原膜
- ⚠️ 可优化：
  - 与原膜区分管理
  - 支持不同的计算规则

---

### 十、优先级排序

#### 🔴 **高优先级**（立即实施）

1. **数据一致性**：
   - 统一单位命名规范
   - 修正拼写错误（safty → safety）
   - 添加数据库约束

2. **性能优化**：
   - 添加数据库索引
   - 使用 `read_group` 优化聚合计算
   - 优化计算字段依赖关系

3. **业务逻辑**：
   - 统一单位管理系统
   - 自动单位转换机制

#### 🟡 **中优先级**（近期实施）

4. **用户体验**：
   - 优化库存移动界面
   - 改进产品视图
   - 添加筛选和分组功能

5. **集成优化**：
   - 与标准UoM系统集成
   - 与采购/销售模块集成

#### 🟢 **低优先级**（长期优化）

6. **代码质量**：
   - 减少 hasattr 使用
   - 细化异常处理
   - 添加单元测试

7. **报表功能**：
   - 基于附加单位的报表
   - 库存周转分析

---

## 📋 实施建议

### 阶段1：基础优化（1-2周）
- 统一字段命名
- 修正拼写错误
- 添加数据库索引
- 优化计算字段性能

### 阶段2：功能增强（2-3周）
- 统一单位管理系统
- 自动单位转换
- 优化用户界面

### 阶段3：深度集成（3-4周）
- 与标准UoM系统集成
- 与采购/销售模块集成
- 报表功能开发

---

**审查完成日期**: 2025-11-04  
**建议审查周期**: 每季度审查一次
