# -*- coding: utf-8 -*-
{
    'name': '制造订单剩余产品返回处理',
    'version': '2.0',
    'summary': '智能处理制造订单剩余产品，支持多种返回策略',
    'description': '''
        制造订单剩余产品返回处理模块 v2.0
        
        🎯 核心功能：
        • 智能剩余产品检测与处理
        • 多种返回策略（不良品仓/主仓库/自定义位置）
        • 自动库存调拨单创建
        • 详细处理日志记录
        • 批量处理支持
        
        🔧 优化特性：
        • 智能位置推荐
        • 数量验证与提示
        • 处理历史追踪
        • 用户友好的向导界面
        • 权限控制与安全
        
        📊 业务价值：
        • 减少库存浪费
        • 提高生产效率
        • 优化库存管理
        • 增强数据追溯
    ''',
    'author': 'memory',
    'website': 'https://www.example.com',
    'category': 'Manufacturing/Inventory',
    'depends': ['mrp', 'stock', 'stock_account', 'stock_unit_mgmt'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/return_reason_data.xml',
        'views/mrp_production_views.xml',
        'views/mrp_production_return_wizard_views.xml',
        'views/mrp_production_return_wizard_line_views.xml',
        'views/mrp_production_return_history_views.xml',
        'views/mrp_consumption_warning_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
    'images': ['static/description/banner.png'],
    'price': 0,
    'currency': 'EUR',
}
