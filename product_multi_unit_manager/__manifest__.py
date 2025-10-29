# -*- coding: utf-8 -*-
{
    'name': '产品多单位管理器',
    'version': '1.0',
    'summary': '产品多计量单位快速设置',
    'description': """
        产品多单位管理器
        ===============

        本模块提供了一种快速简便的方式来为产品设置多个计量单位。
            """,
    'author': 'Grit',
    'website': 'https://ifangtech.com',
    'category': 'Inventory/Inventory',
    'depends': ['product', 'uom'],
    'data': [
        'security/ir.model.access.csv',
        'data/uom_data.xml',
        'wizard/product_unit_setup_wizard_views.xml',
        'views/product_template_views.xml',
        'views/menu_views.xml',
    ],
    # 'demo': [
    #     'demo/demo_data.xml',
    # ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
