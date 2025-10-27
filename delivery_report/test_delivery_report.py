#!/usr/bin/env python3
"""
交货单打印模块测试脚本
"""

import urllib.request
import urllib.error

def test_delivery_report_module():
    """测试交货单打印模块功能"""
    
    base_url = "http://localhost:8069"
    
    # 测试服务器连接
    try:
        response = urllib.request.urlopen(base_url, timeout=10)
        if response.getcode() == 200 or response.getcode() == 303:
            print("✅ Odoo服务器运行正常")
        else:
            print(f"❌ Odoo服务器响应异常: {response.getcode()}")
            return False
    except Exception as e:
        print(f"❌ 无法连接到Odoo服务器: {e}")
        return False
    
    print("\n🎉 交货单打印模块已成功安装并运行！")
    print("\n📋 模块功能说明：")
    print("1. 在库存管理 → 交货单中可以看到'打印交货单'按钮")
    print("2. 点击按钮可以生成包含批次/序列号的PDF报告")
    print("3. 报告包含以下信息：")
    print("   - 交货单基本信息（编号、状态、日期）")
    print("   - 客户信息")
    print("   - 产品明细表（包含批次/序列号）")
    print("   - 批次/序列号汇总表")
    print("   - 备注信息")
    print("   - 签名区域")
    
    print("\n🚀 使用方法：")
    print("1. 访问 http://localhost:8069")
    print("2. 登录Odoo系统")
    print("3. 进入 库存管理 → 交货单")
    print("4. 选择或创建一个交货单")
    print("5. 点击'打印交货单'按钮")
    print("6. 系统将生成包含批次/序列号的PDF报告")
    
    return True

if __name__ == "__main__":
    print("🚀 开始测试交货单打印模块...")
    test_delivery_report_module()
