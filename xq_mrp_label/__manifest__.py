# -*- coding: utf-8 -*-
{
    'name': 'XQ MRP Label (100x100)',
    'version': '18.0.1.0.0',
    'summary': 'Print 100x100mm manufacturing order label',
    'description': 'Adds a PDF label report (100x100mm) for MOs with name, spec, qty, lot.',
    'author': 'Your Company',
    'website': 'https://example.com',
    'category': 'Manufacturing',
    'depends': ['mrp', 'stock', 'quality_control', 'mrp_workorder', 'web'],
    'data': [
        'data/quality_control_data.xml',
        'views/quality_views.xml',
        'views/byproduct_label_wizard_views.xml',
        'report/mrp_label.xml',
        'report/mrp_qc_label.xml',
        'report/mrp_byproduct_label.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'xq_mrp_label/static/src/components/print_label/**/*.xml',
            'xq_mrp_label/static/src/components/print_label/**/*.js',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}


