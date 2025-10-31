# 库存单位管理器 (Stock Unit Management)

## 模块说明

本模块整合了以下三个模块的功能：
- `stock_extend` - 库存扩展模块
- `bi_product_secondary_uom` - 产品第二单位管理
- `product_multi_unit_manager` - 产品多单位管理器

## 整合时间
- 创建日期：2025-10-30
- 版本：1.0.0

## 核心功能

### 1. 产品单位配置
- 支持多种预定义单位（卷、桶、箱、袋、公斤、平方米等）
- 支持自定义单位
- 快速配置向导
- 单位配置模板

### 2. 库存移动单位管理
- 库存移动行单位信息记录
- 批次/序列号与单位关联
- 单位数量自动计算
- 动态单位选择和验证

### 3. 第二单位系统
- 产品第二单位配置
- 第二单位数量自动计算
- 销售和采购订单第二单位显示
- 库存数量第二单位统计

### 4. 产品属性管理
- 产品类型分类（原膜、成品膜、配液原料）
- 产品尺寸管理（宽度、长度、厚度）
- 自动计算面积和体积
- 材料密度和固含量管理

### 5. 库存统计
- 实际卷数统计
- 桶数统计
- 单位数量汇总
- 安全库存预警

## 主要改进

### 相比原模块的优势
1. **统一管理** - 所有单位相关功能集中在一个模块
2. **消除冲突** - 解决了字段重复和视图冲突问题
3. **代码优化** - 重构和整合了重复代码
4. **易于维护** - 统一的代码结构和命名规范
5. **功能增强** - 改进了单位选择和验证逻辑

### 技术改进
- 使用动态单位选择（基于产品配置）
- 改进的字段命名（避免冲突）
- 统一的计算逻辑
- 优化的视图继承结构
- 更好的权限管理

## 安装说明

### 前置条件
确保以下模块已安装：
- `base`
- `product`
- `stock`
- `uom`
- `sale_management`
- `purchase`
- `stock_account`

### 安装步骤
1. 禁用旧模块（已完成）：
   ```bash
   # 三个旧模块已重命名为 .disabled 后缀
   - stock_extend.disabled
   - bi_product_secondary_uom.disabled
   - product_multi_unit_manager.disabled
   ```

2. 安装新模块：
   ```bash
   sudo -u odoo /usr/bin/python3 /usr/bin/odoo -d <database> -i stock_unit_mgmt --stop-after-init
   ```

3. 重启Odoo服务：
   ```bash
   sudo systemctl restart odoo
   ```

## 数据迁移

### 保留的数据表
- `product_template` - 产品模板字段
- `stock_move_line` - 库存移动行字段
- `stock_quant` - 库存数量字段
- `sale_order_line` - 销售订单行字段
- `purchase_order_line` - 采购订单行字段

### 字段映射
| 旧字段 | 新字段 | 说明 |
|--------|--------|------|
| `lot_weight` | 已移除 | 使用 `lot_quantity` 替代 |
| `lot_barrels` | `lot_quantity` | 统一为通用单位数量 |
| `custom_unit_name` | `lot_unit_name` + `lot_unit_name_custom` | 拆分为选择和自定义 |
| `secondary_uom` | 保留 | 第二单位功能 |
| `enable_custom_units` | 保留 | 自定义单位开关 |

## 使用指南

### 1. 配置产品单位
```
产品 → 产品模板 → 采购标签页 → 附加单位配置
- 启用自定义单位
- 选择默认单位配置（卷/桶/箱等）
- 设置单位数值和类型
```

### 2. 库存移动时填写单位
```
库存 → 调拨单 → 详细操作 → 单位名称 + 单位数量
- 系统自动根据产品配置填充单位
- 支持手动调整单位数量
- 单位信息自动传递到库存数量
```

### 3. 查看库存单位统计
```
库存 → 产品 → 在手库存
- 查看自定义单位列
- 查看单位数量汇总
- 查看第二单位统计
```

### 4. 使用单位配置向导
```
库存 → 库存单位管理 → 单位配置向导
- 批量选择产品
- 统一设置单位配置
- 快速应用到多个产品
```

## 技术架构

### 模型结构
```
models/
├── product_template.py      # 产品模板扩展
├── stock_move.py            # 库存移动扩展
├── stock_move_line.py       # 库存移动行扩展
├── stock_quant.py           # 库存数量扩展
├── sale_order_line.py       # 销售订单行扩展
└── purchase_order_line.py   # 采购订单行扩展
```

### 视图结构
```
views/
├── product_template_views.xml      # 产品视图
├── stock_move_views.xml            # 库存移动视图
├── stock_quant_views.xml           # 库存数量视图
├── sale_order_line_views.xml       # 销售订单视图
├── purchase_order_line_views.xml   # 采购订单视图
└── menu_views.xml                  # 菜单配置
```

### 向导
```
wizard/
├── product_unit_setup_wizard.py           # 单位配置向导
└── product_unit_setup_wizard_views.xml    # 向导视图
```

## 常见问题

### Q1: 如何恢复旧模块？
A: 将 `.disabled` 后缀删除，重新启用模块。但不建议这样做，因为会导致字段冲突。

### Q2: 旧数据会丢失吗？
A: 不会。字段数据仍然保留在数据库中，新模块继续使用相同的字段。

### Q3: 如何自定义单位？
A: 在产品配置中选择"自定义"单位类型，然后填写自定义单位名称。

### Q4: 第二单位和自定义单位有什么区别？
A: 
- **第二单位**：基于Odoo原生UoM系统，用于自动单位转换
- **自定义单位**：独立的单位系统，用于记录实际包装单位（如卷数、桶数）

## 支持与维护

### 版本兼容性
- Odoo 18.0+

### 许可证
LGPL-3

### 作者
Grit - https://ifangtech.com

## 更新日志

### v1.0.0 (2025-10-30)
- ✅ 初始版本发布
- ✅ 整合三个模块功能
- ✅ 优化代码结构
- ✅ 改进单位选择逻辑
- ✅ 添加单位配置向导
- ✅ 完善文档说明

## 后续规划

### v1.1.0 (计划中)
- [ ] 添加单位转换规则配置
- [ ] 支持批量导入单位配置
- [ ] 添加单位使用情况报表
- [ ] 优化性能和查询效率

### v1.2.0 (计划中)
- [ ] 支持多级单位（如：箱→盒→件）
- [ ] 添加单位条码支持
- [ ] 集成RFID单位管理
- [ ] 移动端单位输入优化

