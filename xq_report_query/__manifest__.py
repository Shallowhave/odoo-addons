{
    'name': '生产与库存报表查询',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Reporting',
    'summary': '按业务日期查询生产、原膜、成品和涂液报表并导出 Excel',
    'description': """
        生产与库存报表查询
        ==================
        从制造、库存和采购业务数据生成报表模板中的六类明细报表，
        支持按日期、仓库、产品分类、产品和批次查询，并导出 Excel。
    """,
    'author': 'memory',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'stock',
        'mrp',
        'purchase',
        'stock_unit_mgmt',
        'quality_report',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/report_query_security.xml',
        'views/report_query_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
