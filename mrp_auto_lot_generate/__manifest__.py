{
    'name': '生产自动批次生成器',
    'version': '2.0',
    'summary': '当组件就绪时自动生成完成品批次号（监听移动行）',
    'description': '''
        增强版生产自动批次生成器，具有以下特性：
        - 可配置批次号前缀
        - 改进的错误处理
        - 更好的性能
        - 增强的日志记录
        - 配置界面
    ''',
    'author': 'memory',
    'depends': ['mrp', 'stock'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
