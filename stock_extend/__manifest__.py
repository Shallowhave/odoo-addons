# -*- coding: utf-8 -*-
{
    'name': "stock_extend",

    'summary': "Short (1 phrase/line) summary of the module's purpose",

    'description': """
Long description of module's purpose
    """,

    'author': "My Company",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base','stock'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'views/views.xml',
        'views/stock_quant_views.xml',
        'views/templates.xml',
    ],
    # client-side templates
    'qweb': ['static/src/xml/lots_dialog.xml'],
    # static files
    'assets': {
        'web.assets_backend': [
            'stock_extend/static/src/xml/lots_dialog.xml',
            'stock_extend/static/src/js/stock_move_line_unit_filter.js',
        ],
    },
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}

