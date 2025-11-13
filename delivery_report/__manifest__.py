{
    'name': '交货单打印增强',
    'version': '1.0',
    'category': 'Inventory',
    'summary': '增强交货单打印功能，包含批次/序列号信息',
    'description': """
        Odoo 18 交货单打印增强模块
        ====================
        此模块为交货单添加了以下功能：
        - 在交货单中显示产品的批次/序列号
        - 创建专业的交货单打印模板
        - 支持批次/序列号的详细显示
        - 集成到现有的库存管理流程
    """,
    'author': 'memory',
    'website': 'http://www.yourwebsite.com',
    'depends': ['base', 'stock', 'sale', 'account', 'stock_unit_mgmt'],
    'data': [
        'security/ir.model.access.csv',
        'data/delivery_report_data.xml',
        'views/stock_picking_type_views.xml',
        'views/stock_picking_views.xml',
        'reports/delivery_report.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
