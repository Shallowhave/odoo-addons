# -*- coding: utf-8 -*-
{
    'name': '发货标签',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': '发货标签打印和管理',
    'description': """
发货标签模块
============

功能特性:
--------
* 创建和管理发货标签
* 支持多种标签模板
* 打印发货标签
* 与销售订单和发货单集成
* 支持条形码和二维码
    """,
    'author': 'memory',
    'website': 'https://www.yourcompany.com',
    'depends': [
        'base',
        'stock',
        'sale',
        'delivery',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/delivery_label_security.xml',
        'data/delivery_label_data.xml',
        'views/delivery_label_views.xml',
        'views/delivery_label_template_views.xml',
        'views/stock_picking_views.xml',
        'views/menu_views.xml',
        'reports/delivery_label_report.xml',
    ],
    'demo': [
        'demo/delivery_label_demo.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
