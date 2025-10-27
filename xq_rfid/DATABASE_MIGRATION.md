# 数据库迁移说明 - 移除不合理的唯一约束

**更新日期**: 2025-10-17  
**问题**: 旧版本中 `product_id` 和 `picking_id` 的唯一约束导致无法为同一产品创建多个 RFID 标签

---

## ⚠️ 问题描述

旧版本的 `rfid.tag` 表中有两个不合理的唯一约束：

1. **`rfid_tag_uniq_product`** - 一个产品只能有一个 RFID
2. **`rfid_tag_uniq_picking`** - 一个调拨单只能有一个 RFID

**为什么不合理？**
- 同一个产品可以有多个生产批次
- 每个批次都应该有自己的 RFID 标签
- 这两个约束会阻止为同一产品的不同批次生成 RFID

---

## ✅ 解决方案

已从代码中移除这两个约束，只保留合理的约束：

1. **`rfid_tag_uniq_name`** - RFID 编号必须唯一 ✅（保留）
2. **`rfid_tag_uniq_stock_prod_lot`** - 一个批次只能有一个 RFID ✅（保留）

---

## 🔧 数据库迁移步骤

### 方案 1：自动迁移（推荐）

升级模块后，Odoo 会自动尝试移除这些约束。如果遇到错误，请使用方案 2。

```bash
# 升级模块
./odoo-bin -u xq_rfid -d your_database
```

### 方案 2：手动删除约束（如果自动迁移失败）

如果升级时出现约束冲突错误，请按以下步骤手动删除约束：

#### 步骤 1：连接到数据库

```bash
psql -U odoo -d your_database
```

#### 步骤 2：检查现有约束

```sql
-- 查看 rfid_tag 表的所有约束
SELECT 
    conname AS constraint_name,
    contype AS constraint_type,
    pg_get_constraintdef(oid) AS constraint_definition
FROM pg_constraint
WHERE conrelid = 'rfid_tag'::regclass
ORDER BY conname;
```

#### 步骤 3：删除不合理的约束

```sql
-- 删除产品唯一约束
ALTER TABLE rfid_tag DROP CONSTRAINT IF EXISTS rfid_tag_uniq_product;

-- 删除调拨单唯一约束
ALTER TABLE rfid_tag DROP CONSTRAINT IF EXISTS rfid_tag_uniq_picking;
```

#### 步骤 4：验证约束已删除

```sql
-- 再次检查约束，应该只剩下 name 和 stock_prod_lot_id 的唯一约束
SELECT 
    conname AS constraint_name,
    contype AS constraint_type,
    pg_get_constraintdef(oid) AS constraint_definition
FROM pg_constraint
WHERE conrelid = 'rfid_tag'::regclass
ORDER BY conname;
```

**期望结果**：应该只看到这些约束：
- `rfid_tag_uniq_name` - UNIQUE (name)
- `rfid_tag_uniq_stock_prod_lot` - UNIQUE (stock_prod_lot_id)
- ❌ 不应该看到 `rfid_tag_uniq_product`
- ❌ 不应该看到 `rfid_tag_uniq_picking`

#### 步骤 5：退出数据库

```sql
\q
```

---

## 🔍 验证迁移

迁移完成后，测试以下场景：

### 测试 1：同一产品的多个批次

```
1. 创建生产订单 MO001
   - 产品：产品 A
   - 批次号：LOT001
   
2. 执行到 RFID 质检点，点击"通过"
   - 应该成功生成 RFID000001
   
3. 创建生产订单 MO002
   - 产品：产品 A（同一个产品）
   - 批次号：LOT002（不同批次）
   
4. 执行到 RFID 质检点，点击"通过"
   - ✅ 应该成功生成 RFID000002（之前会失败）
```

### 测试 2：检查约束仍然有效

```
1. 创建生产订单 MO003
   - 产品：产品 B
   - 批次号：LOT003
   
2. 执行到 RFID 质检点，点击"通过"
   - 应该成功生成 RFID000003
   
3. 再次对同一批次执行 RFID 质检
   - ❌ 应该提示错误：一个批次只能有一个 RFID（这是正确的）
```

---

## 📊 对现有数据的影响

### 已有数据

- ✅ 已存在的 RFID 标签不受影响
- ✅ 数据完整性保持不变
- ✅ 已关联的产品、批次、生产订单关系不变

### 约束变更

| 约束 | 之前 | 现在 | 影响 |
|------|------|------|------|
| RFID 编号唯一 | ✅ 有 | ✅ 保留 | 无影响 |
| 批次号唯一 | ✅ 有 | ✅ 保留 | 无影响 |
| 产品唯一 | ⚠️ 有 | ❌ 移除 | **允许同一产品有多个 RFID** |
| 调拨单唯一 | ⚠️ 有 | ❌ 移除 | **允许同一调拨单有多个 RFID** |

---

## 🚨 故障排除

### 问题 1：升级时报错"约束冲突"

**错误信息**：
```
psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint "rfid_tag_uniq_product"
```

**解决方法**：
使用方案 2 手动删除约束

### 问题 2：删除约束后仍然报错

**可能原因**：缓存未清除

**解决方法**：
```bash
# 重启 Odoo 服务
sudo systemctl restart odoo

# 或
./odoo-bin -u xq_rfid -d your_database --stop-after-init
```

### 问题 3：找不到约束

**错误信息**：
```
ERROR: constraint "rfid_tag_uniq_product" of relation "rfid_tag" does not exist
```

**说明**：约束可能已经被删除了，这是正常的。继续下一步即可。

---

## 📝 SQL 快速脚本

如果你熟悉 SQL，可以直接运行这个完整脚本：

```sql
-- 备份当前约束信息（可选）
CREATE TABLE IF NOT EXISTS rfid_tag_constraints_backup AS
SELECT 
    conname,
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'rfid_tag'::regclass;

-- 删除不合理的约束
ALTER TABLE rfid_tag DROP CONSTRAINT IF EXISTS rfid_tag_uniq_product;
ALTER TABLE rfid_tag DROP CONSTRAINT IF EXISTS rfid_tag_uniq_picking;

-- 验证结果
SELECT 
    conname AS constraint_name,
    pg_get_constraintdef(oid) AS constraint_definition
FROM pg_constraint
WHERE conrelid = 'rfid_tag'::regclass
AND conname LIKE 'rfid_tag_uniq%'
ORDER BY conname;
```

---

## ✅ 迁移检查清单

完成迁移后，请检查以下项目：

- [ ] 升级模块成功（无错误）
- [ ] 数据库约束已更新（只剩 name 和 stock_prod_lot_id）
- [ ] 可以为同一产品的不同批次生成 RFID
- [ ] 批次号唯一约束仍然有效
- [ ] RFID 编号唯一约束仍然有效
- [ ] 已有数据完整无损

---

## 📞 技术支持

如果迁移过程中遇到问题，请联系：

- **开发者**: Grit
- **官网**: https://ifangtech.com
- **文档**: 查看模块目录下的其他 Markdown 文件

---

## 📌 重要提示

⚠️ **在生产环境操作前，请务必备份数据库！**

```bash
# 备份数据库
pg_dump -U odoo -d your_database > backup_$(date +%Y%m%d_%H%M%S).sql

# 如需恢复
psql -U odoo -d your_database < backup_YYYYMMDD_HHMMSS.sql
```

---

**迁移完成后，您就可以为同一产品的不同批次生成多个 RFID 标签了！** ✨

