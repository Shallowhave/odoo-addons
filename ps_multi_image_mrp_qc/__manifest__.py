# -*- coding: utf-8 -*-

{
    # Module Info
    'name': "Multi Image QC",
    'version': '18.0.1.0.0',
    'category': 'Extra tools',
    'summary': 'Enables multi-image selection during MRP quality checks.',
    'description': 'This module enhances the MRP quality check process by allowing users to select and manage multiple images simultaneously, improving efficiency and flexibility in quality control workflows.',

    #Author
    'author': 'PySquad Informatics LLP',
    'company': 'PySquad Informatics LLP',
    'maintainer': 'PySquad Informatics LLP',
    'website': 'https://www.pysquad.com/',
    'sequence': -104,

    # Dependencies
    'depends': ['base', 'web', 'quality_control', 'mrp', 'mrp_workorder'],

    # Data
    'data': [
        'data/quality_control_data.xml',
        'views/multi_image_view.xml',
    ],

    'assets': {
        'web.assets_backend': [
            'ps_multi_image_mrp_qc/static/src/**/*.xml',
            'ps_multi_image_mrp_qc/static/src/**/*.js',
            'ps_multi_image_mrp_qc/static/src/css/table_image_field.css',
        ]},
    

    # Images
    'images': [
        'static/description/banner.png',
    ],

    # Technical Info
    'license': 'LGPL-3',
    'installable': True,
    'application': True,
}
