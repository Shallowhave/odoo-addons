# -*- coding: utf-8 -*-
{
    'name': '库存单位管理器',
    'version': '1.0.0',
    'summary': '统一的产品多单位管理和库存单位扩展',
    'description': """
        库存单位管理器
        =============
        
        核心：
        • 产品附加单位配置
        • 库存移动行记录单位名称与单位数量
        • 库存数量视图展示并汇总单位数量
    """,
    'author': 'memory',
    'website': 'https://ifangtech.com',
    'category': 'Inventory/Inventory',
    'depends': [
        'base',
        'product', 
        'stock',
        'uom',
        'purchase',
        'sale'
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/uom_data.xml',
        'views/product_template_views.xml',
        'views/stock_move_views.xml',
        'views/stock_quant_views.xml',
        'wizard/product_unit_setup_wizard_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
