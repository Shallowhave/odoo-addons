import os
import sys
import ast
import xml.etree.ElementTree as ET

def check_file_exists(file_path):
    return os.path.exists(file_path)

def check_python_syntax(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=file_path)
        return True
    except SyntaxError as e:
        print(f"❌ Python Syntax Error in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False

def check_manifest(manifest_path):
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest_content = f.read()
        manifest = ast.literal_eval(manifest_content)
        
        required_keys = ['name', 'version', 'depends', 'data']
        for key in required_keys:
            if key not in manifest:
                print(f"❌ Manifest file is missing key: {key}")
                return False
        return True
    except Exception as e:
        print(f"❌ Error parsing manifest file {manifest_path}: {e}")
        return False

def check_xml_syntax(file_path):
    try:
        ET.parse(file_path)
        return True
    except ET.ParseError as e:
        print(f"❌ XML Syntax Error in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False

def run_tests():
    print("🚀 开始测试交货单打印模块...")
    module_path = os.path.dirname(os.path.abspath(__file__))
    
    # 1. 检查文件结构
    print("\n🔍 检查模块文件结构...")
    files_to_check = {
        "__manifest__.py": False,
        "__init__.py": False,
        "models/__init__.py": False,
        "models/stock_picking.py": False,
        "views/stock_picking_views.xml": False,
        "reports/delivery_report.xml": False,
        "security/ir.model.access.csv": False,
        "data/delivery_report_data.xml": False,
    }

    all_files_exist = True
    for f in files_to_check:
        path = os.path.join(module_path, f)
        if check_file_exists(path):
            files_to_check[f] = True
            print(f"✅ {f}")
        else:
            all_files_exist = False
            print(f"❌ {f} - 文件不存在")
    
    if not all_files_exist:
        print("❌ 文件结构检查失败。")
        return False

    # 2. 检查Python语法
    print("\n🔍 检查Python语法...")
    python_files = [
        os.path.join(module_path, "__init__.py"),
        os.path.join(module_path, "models/__init__.py"),
        os.path.join(module_path, "models/stock_picking.py"),
    ]
    all_python_syntax_ok = True
    for f in python_files:
        if check_python_syntax(f):
            print(f"✅ {f} - 语法正确")
        else:
            all_python_syntax_ok = False
    
    if not all_python_syntax_ok:
        print("❌ Python语法检查失败。")
        return False

    # 3. 检查清单文件
    print("\n🔍 检查清单文件...")
    manifest_path = os.path.join(module_path, "__manifest__.py")
    if check_manifest(manifest_path):
        print("✅ 清单文件包含 name")
        print("✅ 清单文件包含 version")
        print("✅ 清单文件包含 depends")
        print("✅ 清单文件包含 data")
    else:
        print("❌ 清单文件检查失败。")
        return False

    # 4. 检查XML语法
    print("\n🔍 检查XML语法...")
    xml_files = [
        os.path.join(module_path, "views/stock_picking_views.xml"),
        os.path.join(module_path, "reports/delivery_report.xml"),
        os.path.join(module_path, "data/delivery_report_data.xml"),
    ]
    all_xml_syntax_ok = True
    for f in xml_files:
        if check_xml_syntax(f):
            print(f"✅ {f} - 语法正确")
        else:
            all_xml_syntax_ok = False
    
    if not all_xml_syntax_ok:
        print("❌ XML语法检查失败。")
        return False

    print("\n==================================================")
    print("📊 测试结果: 4/4 通过")
    print("🎉 所有测试通过！模块结构正确。")
    return True

if __name__ == "__main__":
    run_tests()
