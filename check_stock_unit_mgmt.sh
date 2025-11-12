#!/bin/bash
# 诊断 stock_unit_mgmt 模块的脚本

MODULE_NAME="stock_unit_mgmt"
MODULE_PATH="/opt/custom/addons/$MODULE_NAME"

echo "========================================="
echo "检查 $MODULE_NAME 模块"
echo "========================================="
echo ""

# 1. 检查模块目录
echo "1. 检查模块目录结构..."
if [ ! -d "$MODULE_PATH" ]; then
    echo "   ❌ 模块目录不存在: $MODULE_PATH"
    exit 1
fi
echo "   ✓ 模块目录存在"

# 2. 检查关键文件
echo ""
echo "2. 检查关键文件..."
files=(
    "__init__.py"
    "__manifest__.py"
    "models/__init__.py"
    "models/product_template.py"
    "models/stock_move.py"
    "models/stock_move_line.py"
    "models/stock_quant.py"
    "models/utils.py"
    "security/ir.model.access.csv"
)

missing_files=()
for file in "${files[@]}"; do
    if [ ! -f "$MODULE_PATH/$file" ]; then
        missing_files+=("$file")
        echo "   ❌ 缺失: $file"
    fi
done

if [ ${#missing_files[@]} -eq 0 ]; then
    echo "   ✓ 所有关键文件存在"
else
    echo "   ❌ 发现 ${#missing_files[@]} 个缺失文件"
fi

# 3. 检查 Python 语法
echo ""
echo "3. 检查 Python 语法..."
python_files=$(find "$MODULE_PATH" -name "*.py" -not -path "*/__pycache__/*")
syntax_errors=0
for py_file in $python_files; do
    if ! python3 -m py_compile "$py_file" 2>&1 | grep -q .; then
        # 语法检查通过
        :
    else
        echo "   ❌ 语法错误: $py_file"
        python3 -m py_compile "$py_file" 2>&1
        syntax_errors=$((syntax_errors + 1))
    fi
done

if [ $syntax_errors -eq 0 ]; then
    echo "   ✓ Python 语法检查通过"
else
    echo "   ❌ 发现 $syntax_errors 个语法错误"
fi

# 4. 检查 XML 语法
echo ""
echo "4. 检查 XML 文件..."
xml_files=$(find "$MODULE_PATH" -name "*.xml")
xml_errors=0
for xml_file in $xml_files; do
    if ! xmllint --noout "$xml_file" 2>&1 | grep -q .; then
        # XML 语法检查通过
        :
    else
        echo "   ❌ XML 语法错误: $xml_file"
        xmllint --noout "$xml_file" 2>&1
        xml_errors=$((xml_errors + 1))
    fi
done

if [ $xml_errors -eq 0 ]; then
    echo "   ✓ XML 语法检查通过"
else
    echo "   ❌ 发现 $xml_errors 个 XML 语法错误"
fi

# 5. 检查 manifest 文件
echo ""
echo "5. 检查 __manifest__.py..."
if python3 -c "
import sys
sys.path.insert(0, '$MODULE_PATH')
try:
    import ast
    with open('$MODULE_PATH/__manifest__.py', 'r', encoding='utf-8') as f:
        code = f.read()
    manifest = ast.literal_eval(code.split('{', 1)[1].rsplit('}', 1)[0] if '{' in code else '{}')
    print('   ✓ Manifest 文件格式正确')
    print(f'   - 模块名: {manifest.get(\"name\", \"N/A\")}')
    print(f'   - 版本: {manifest.get(\"version\", \"N/A\")}')
    print(f'   - 依赖: {manifest.get(\"depends\", [])}')
except Exception as e:
    print(f'   ❌ Manifest 文件错误: {e}')
    sys.exit(1)
" 2>&1; then
    :
else
    echo "   ❌ Manifest 文件检查失败"
fi

# 6. 检查模块是否已安装
echo ""
echo "6. 检查模块安装状态..."
if sudo -u odoo /usr/bin/odoo -c /etc/odoo/odoo.conf -d odoo-test --stop-after-init --log-level=error 2>&1 | grep -q "stock_unit_mgmt"; then
    echo "   ✓ 模块已安装"
else
    echo "   ⚠ 无法确定模块安装状态（需要查看数据库）"
fi

# 7. 尝试加载模块（测试模式）
echo ""
echo "7. 尝试测试加载模块..."
test_output=$(sudo -u odoo /usr/bin/odoo -c /etc/odoo/odoo.conf -d odoo-test -u "$MODULE_NAME" --stop-after-init --log-level=error 2>&1 | tail -20)
if echo "$test_output" | grep -qi "error\|exception\|traceback\|fail"; then
    echo "   ⚠ 发现可能的错误:"
    echo "$test_output" | grep -i "error\|exception\|traceback\|fail" | head -5
else
    echo "   ✓ 模块加载测试通过"
fi

echo ""
echo "========================================="
echo "检查完成"
echo "========================================="

