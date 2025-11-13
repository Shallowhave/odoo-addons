# -*- coding: utf-8 -*-
{
    'name': '组件扫码确认',
    'version': '1.0.0',
    'summary': '生产过程中扫码确认组件是否匹配',
    'description': """
        组件扫码确认模块
        ===============
        
        核心功能：
        • 在生产过程中通过扫码确认组件是否匹配BOM
        • 验证扫码的组件是否在生产订单的组件列表中
        • 记录验证结果和扫码日志
        • 与生产订单、工单、工序集成
    """,
    'author': 'memory',
    'website': 'https://ifangtech.com',
    'category': 'Manufacturing',
    'depends': [
        'base',
        'mrp',
        'quality_control',
        'mrp_workorder',
        'stock_barcode',  # 支持条码扫描功能
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/quality_test_type.xml',
        'views/quality_point_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mrp_component_scan_verify/static/src/components/**/*.xml',
            'mrp_component_scan_verify/static/src/components/**/*.js',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}

