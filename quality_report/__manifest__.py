{
    'name': '品质报告打印',
    'version': '1.0',
    'category': 'Inventory/Quality',
    'summary': '为交货单添加品质报告打印功能',
    'description': """
        Odoo 18 品质报告打印模块
        ====================
        此模块扩展了Odoo的库存模块，为交货单添加了以下功能：
        - 在交货单中打印品质报告
        - 显示产品的批号/序列号和品质备注
        - 提供专业的品质报告打印模板
    """,
    'author': 'memory',
    'website': 'http://www.yourwebsite.com',
    'depends': ['stock', 'delivery_report'],
    'data': [
        'security/ir.model.access.csv',
        'views/stock_picking_type_views.xml',
        'views/stock_picking_views.xml',
        'reports/quality_report_data.xml',
        'reports/quality_report.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
