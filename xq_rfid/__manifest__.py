# -*- coding: utf-8 -*-

{
    'name': "RFID 标签管理",

    'summary': """Odoo RFID 标签集成管理系统""",

    'description': """
        RFID 标签管理模块
        ================
        
        本模块为产品、库存、生产提供 RFID 标签管理功能。
        
        主要功能：
        - RFID 标签统一管理
        - 产品、调拨单、批次/序列号 RFID 绑定
        - 质量检查点集成 RFID 生成（手动）
        - RFID 硬件设备接口（预留）
        - 批次溯源（记录生产订单和生产日期）
        
        质检集成：
        - 通过质量检查点手动生成 RFID 标签
        - RFID 与序列号、质检记录、生产订单关联
        - 支持 RFID 设备写入（可选）
        
        硬件对接：
        - 抽象设备接口，支持多种 RFID 读写器
        - 设备配置管理（USB/串口/网络）
        - 读取、写入、验证 RFID 标签
        - 预留扩展接口，便于集成实际硬件
    """,

    'author': "Grit",
    'website': "https://ifangtech.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/14.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Technical',
    'version': '18.0.1.0.0',

    # any module necessary for this one to work correctly
    'depends': [
        'base',
        'product',
        'stock',
        'mrp',  # 生产模块
        'quality_control',  # 质检模块
        'mrp_workorder',  # 生产工单模块（用于质检对话框）
    ],

    # Frontend assets
    'assets': {
        'web.assets_backend': [
            'xq_rfid/static/src/components/**/*.xml',
            'xq_rfid/static/src/components/**/*.js',
            'xq_rfid/static/src/css/rfid_generation_wizard.css',
        ],
    },

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'data/rfid_sequence.xml',  # RFID 编号序列
        'data/quality_test_type.xml',      # 质量检查类型
        'views/rfid_menu_views.xml',       # 菜单结构（不引用 action，必须最先加载）
        'views/product_views.xml',
        'views/product_template_views.xml',
        'views/stock_picking_views.xml',
        'views/stock_lot_views.xml',
        'views/rfid_tag_views.xml',        # RFID 标签视图（包含 action 和菜单项）
        'views/rfid_device_views.xml',     # RFID 设备配置视图（包含 action 和菜单项）
        'views/mrp_production_views.xml',  # 生产订单视图
        'views/quality_check_wizard_views.xml',  # 质检向导视图
    ],
    'application': True,
}
