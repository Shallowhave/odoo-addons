# 🚨 快速修复：约束冲突错误

**错误信息**: `duplicate key value violates unique constraint "rfid_tag_uniq_product"`

---

## ⚡ 快速解决（3 步）

### 步骤 1: 连接数据库

```bash
psql -U odoo -d your_database_name
```

### 步骤 2: 执行以下 SQL

```sql
ALTER TABLE rfid_tag DROP CONSTRAINT IF EXISTS rfid_tag_uniq_product;
ALTER TABLE rfid_tag DROP CONSTRAINT IF EXISTS rfid_tag_uniq_picking;
```

### 步骤 3: 退出数据库

```sql
\q
```

---

## ✅ 完成！

现在重新测试质检流程，应该可以正常生成 RFID 了。

---

## 📖 详细说明

- 查看 `DATABASE_MIGRATION.md` - 完整迁移指南
- 查看 `UPDATE_2025_10_17.md` - 更新说明

---

## 🔍 验证修复

执行以下 SQL 验证约束已删除：

```sql
psql -U odoo -d your_database_name -c "SELECT conname FROM pg_constraint WHERE conrelid = 'rfid_tag'::regclass AND conname LIKE 'rfid_tag_uniq%';"
```

**期望结果**：应该只看到两个约束
- `rfid_tag_uniq_name`
- `rfid_tag_uniq_stock_prod_lot`

---

## 💡 问题原因

旧版本有一个不合理的约束：**一个产品只能有一个 RFID**

但实际上：
- 同一产品可以有多个生产批次
- 每个批次应该有自己的 RFID

现在已修复：**同一产品可以有多个 RFID，但每个批次只能有一个 RFID**

---

**技术支持**: https://ifangtech.com

