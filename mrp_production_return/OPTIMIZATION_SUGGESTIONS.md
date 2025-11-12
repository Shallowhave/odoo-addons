# mrp_production_return 模块优化建议

**审查日期**: 2025-11-06  
**模块版本**: v2.0  
**审查范围**: 完整代码审查

---

## 📊 优化概览

| 优先级 | 优化项 | 工作量 | 预计效果 |
|--------|--------|--------|----------|
| ⭐⭐⭐⭐⭐ | 减少调试日志 | 30分钟 | 减少日志量，提升性能 |
| ⭐⭐⭐⭐ | 提取重复代码 | 1小时 | 代码更易维护 |
| ⭐⭐⭐⭐ | 批量查询优化 | 1小时 | 性能提升 |
| ⭐⭐⭐ | 改进错误处理 | 30分钟 | 更好的用户体验 |
| ⭐⭐⭐ | 代码拆分 | 1小时 | 提高可读性 |

---

## 🔴 高优先级优化

### 1. 减少调试日志（⭐⭐⭐⭐⭐）

**位置**: `mrp_production_return_wizard_line.py`

**问题**:
- 文件中有 **44处** `_logger` 调用
- 大部分是 `INFO` 级别的调试日志
- 在生产环境中会产生大量日志，影响性能

**影响**:
- 日志文件快速增长
- 性能开销（字符串格式化、I/O操作）
- 日志噪音，难以定位真正的问题

**解决方案**:
```python
# 将调试日志改为 DEBUG 级别
_logger.debug(f"[向导行] _compute_available_product_ids 开始: 处理 {len(self)} 条记录")

# 只保留关键错误和警告
_logger.warning(f"[向导行] wizard_id 为空，设置空记录集")
_logger.error(f"[向导行] 错误: {str(e)}", exc_info=True)

# 或者添加日志级别控制
LOG_LEVEL = logging.DEBUG if config.get('debug_mode') else logging.WARNING
```

**优化后**:
- 减少 80% 的日志输出
- 生产环境只记录关键信息
- 开发环境仍可查看详细日志

---

### 2. 提取重复代码（⭐⭐⭐⭐）

**位置**: `mrp_production_return_wizard.py`

**问题**:
- `_process_location_return` 和 `_process_scrap_return` 有大量重复代码
- 两个方法逻辑几乎相同，只是目标位置不同

**当前代码**:
```python
def _process_location_return(self, history, line):
    # 获取源位置
    source_location = self.production_id.location_src_id
    # ... 50行代码 ...

def _process_scrap_return(self, history, line):
    # 获取源位置
    source_location = self.production_id.location_src_id
    # ... 50行几乎相同的代码 ...
```

**解决方案**:
```python
def _create_picking(self, history, line, target_location_id, origin_suffix=''):
    """创建调拨单的通用方法"""
    # 获取源位置
    source_location = self.production_id.location_src_id
    if not source_location:
        raise UserError('无法找到制造订单的源位置')
    
    # 获取公司的默认仓库
    warehouse = self.env['stock.warehouse'].search([
        ('company_id', '=', self.production_id.company_id.id)
    ], limit=1)
    
    if not warehouse:
        raise UserError('无法找到公司的仓库')
    
    # 创建调拨单类型
    picking_type = self.env['stock.picking.type'].search([
        ('code', '=', 'internal'),
        ('warehouse_id', '=', warehouse.id)
    ], limit=1)
    
    if not picking_type:
        raise UserError('无法找到内部调拨单类型')
    
    # 创建库存调拨单
    picking_vals = {
        'picking_type_id': picking_type.id,
        'location_id': source_location.id,
        'location_dest_id': target_location_id,
        'origin': f'制造订单剩余组件{origin_suffix} - {self.production_id.name}',
        'note': f'剩余组件处理\n策略: {dict(self._fields["return_strategy"].selection)[self.return_strategy]}\n原因: {self.return_reason_id.name if self.return_reason_id else self.custom_reason or "无"}',
        'user_id': self.env.user.id,
    }
    
    picking = self.env['stock.picking'].create(picking_vals)
    
    # 创建调拨明细
    move_vals = {
        'name': f'剩余组件{origin_suffix} - {line.product_id.name}',
        'product_id': line.product_id.id,
        'product_uom_qty': line.return_qty,
        'product_uom': line.product_id.uom_id.id,
        'location_id': source_location.id,
        'location_dest_id': target_location_id,
        'picking_id': picking.id,
        'origin': f'制造订单剩余组件{origin_suffix} - {self.production_id.name}',
    }
    
    move = self.env['stock.move'].create(move_vals)
    
    # 更新历史记录
    history.write({
        'picking_id': picking.id,
        'move_id': move.id,
    })
    
    # 自动确认调拨单
    if self.auto_confirm_picking:
        picking.action_confirm()
        
        # 创建移动行并设置完成数量
        move_line_vals = {
            'move_id': move.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_id.uom_id.id,
            'location_id': source_location.id,
            'location_dest_id': target_location_id,
            'qty_done': line.return_qty,
        }
        
        # 如果有批次号，需要处理批次号
        if line.move_id.move_line_ids:
            first_move_line = line.move_id.move_line_ids[0]
            if first_move_line.lot_id:
                move_line_vals['lot_id'] = first_move_line.lot_id.id
        
        self.env['stock.move.line'].create(move_line_vals)
        
        # 完成调拨单
        if picking.state in ('assigned', 'confirmed'):
            picking.button_validate()
    
    return picking, move

def _process_location_return(self, history, line):
    """处理位置返回"""
    self._create_picking(
        history, 
        line, 
        self.target_location_id.id,
        '返回'
    )

def _process_scrap_return(self, history, line):
    """处理报废返回"""
    if not self.scrap_location_id:
        raise UserError('请选择报废仓库位置')
    
    self._create_picking(
        history, 
        line, 
        self.scrap_location_id.id,
        '报废'
    )
```

**优化后**:
- 代码减少 50%
- 逻辑更清晰
- 维护更容易

---

### 3. 批量查询优化（⭐⭐⭐⭐）

**位置**: `mrp_production_return_wizard.py` - `default_get`

**问题**:
- 在循环中多次查询数据库
- `_recommend_defective_location` 和 `_recommend_scrap_location` 可以缓存结果

**当前代码**:
```python
for move in remaining_moves:
    # 每次循环都创建组件行
    component_lines.append((0, 0, {...}))
```

**优化建议**:
```python
# 批量查询已处理的产品（在循环外）
processed_history = self.env['mrp.production.return.history'].search([
    ('production_id', '=', production.id)
])
processed_products = processed_history.mapped('product_id')

# 批量查询仓库（只查询一次）
warehouse = self.env['stock.warehouse'].search([
    ('company_id', '=', production.company_id.id)
], limit=1)

# 批量创建组件行数据
component_lines_data = []
for move in remaining_moves:
    if move.product_id in processed_products:
        continue
    remaining_qty = move.product_uom_qty - move.quantity
    component_lines_data.append({
        'move_id': move.id,
        'product_id': move.product_id.id,
        'return_qty': remaining_qty,
    })

# 批量创建（如果可能）
if component_lines_data:
    component_lines = [(0, 0, data) for data in component_lines_data]
```

---

## 🟡 中优先级优化

### 4. 改进错误处理（⭐⭐⭐）

**位置**: 多个文件

**问题**:
- 某些异常处理不够详细
- 错误消息不够友好

**优化建议**:
```python
# 当前
except Exception as e:
    _logger.error(f"错误: {str(e)}")
    raise UserError(f'处理失败: {str(e)}')

# 优化后
except UserError:
    # 用户错误直接抛出
    raise
except ValidationError:
    # 验证错误直接抛出
    raise
except Exception as e:
    _logger.error(
        f"[剩余组件返回] 处理失败: {str(e)}",
        exc_info=True
    )
    # 友好的错误消息
    raise UserError(
        '处理剩余组件时发生错误。\n'
        '请检查：\n'
        '1. 制造订单状态是否正确\n'
        '2. 目标位置是否有效\n'
        '3. 产品是否有足够的库存\n\n'
        f'技术详情: {str(e)}'
    )
```

---

### 5. 代码拆分（⭐⭐⭐）

**位置**: `mrp_production_return_wizard_line.py` - `_compute_available_product_ids`

**问题**:
- 方法过长（150+ 行）
- 逻辑复杂，难以维护

**优化建议**:
```python
def _get_production_from_wizard(self, wizard):
    """从向导获取制造订单（处理 NewId 等情况）"""
    if wizard.production_id:
        return wizard.production_id
    
    # 尝试从 context 获取
    if 'default_production_id' in self.env.context:
        production_id = self.env.context.get('default_production_id')
        if production_id:
            return self.env['mrp.production'].browse(production_id)
    
    # ... 其他方法
    return None

def _get_remaining_moves(self, production):
    """获取剩余组件移动记录"""
    # 过滤逻辑
    return production.move_raw_ids.filtered(...)

def _compute_available_product_ids(self):
    """计算可用产品列表"""
    for record in self:
        try:
            # 获取制造订单
            production = self._get_production_from_wizard(record.wizard_id)
            if not production:
                record.available_product_ids = record.env['product.product']
                continue
            
            # 获取剩余移动
            remaining_moves = self._get_remaining_moves(production)
            
            # 过滤已处理和已添加的
            available_products = self._filter_available_products(
                remaining_moves, 
                production, 
                record
            )
            
            record.available_product_ids = available_products
        except Exception as e:
            _logger.error(f"计算可用产品列表失败: {str(e)}", exc_info=True)
            record.available_product_ids = record.env['product.product']
```

---

## 🟢 低优先级优化

### 6. 添加缓存机制（⭐⭐）

**位置**: `mrp_production_return_wizard.py` - `_recommend_defective_location`

**建议**:
```python
@api.model
def _get_cached_warehouse_location(self, warehouse, location_type='defective'):
    """获取缓存的仓库位置（避免重复查询）"""
    cache_key = f'warehouse_{warehouse.id}_{location_type}'
    if not hasattr(self.env, '_location_cache'):
        self.env._location_cache = {}
    
    if cache_key not in self.env._location_cache:
        if location_type == 'defective':
            location = self._recommend_defective_location(warehouse)
        else:
            location = self._recommend_scrap_location(warehouse.company_id)
        self.env._location_cache[cache_key] = location
    
    return self.env._location_cache[cache_key]
```

---

### 7. 添加单元测试（⭐）

**建议**:
- 为关键方法添加单元测试
- 测试边界情况
- 测试错误处理

---

## 📋 实施建议

### 立即实施（快速胜利）
1. ✅ **减少调试日志** - 30分钟
2. ✅ **提取重复代码** - 1小时
3. ✅ **改进错误处理** - 30分钟

**预计总时间**: 2小时  
**预期效果**: 立即改善代码质量和性能

### 后续实施
4. 批量查询优化 - 1小时
5. 代码拆分 - 1小时
6. 添加缓存机制 - 1小时

---

## 📊 优化效果预期

| 优化项 | 当前状态 | 优化后 | 改善 |
|--------|----------|--------|------|
| 日志量 | 44处 INFO | 10处 WARNING/ERROR | 减少 77% |
| 代码重复 | 100行重复 | 0行重复 | 减少 100% |
| 方法长度 | 150+ 行 | <50 行 | 减少 67% |
| 数据库查询 | N+1 问题 | 批量查询 | 减少 50% |

---

## ✅ 总结

该模块整体设计良好，功能完整，但存在一些可以优化的地方：

1. **日志过多**：减少调试日志，提升性能
2. **代码重复**：提取公共方法，提高可维护性
3. **性能优化**：批量查询，减少数据库访问
4. **代码质量**：拆分长方法，改进错误处理

通过实施这些优化，可以显著提升模块的性能、可维护性和代码质量。

