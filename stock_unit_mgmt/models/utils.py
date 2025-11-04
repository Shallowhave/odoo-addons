# -*- coding: utf-8 -*-
"""
工具函数模块
提供共享的工具函数和常量
"""

# 单位显示名称映射表（统一管理）
UNIT_DISPLAY_MAP = {
    'kg': '公斤(kg)',
    'roll': '卷',
    'barrel': '桶',
    'box': '箱',
    'bag': '袋',
    'sqm': '平方米(㎡)',
    'piece': '件',
    'custom': '自定义'
}


def get_unit_display_name(unit_code):
    """获取单位显示名称
    
    Args:
        unit_code (str): 单位代码（如：'kg', 'roll', 'barrel' 等）
    
    Returns:
        str: 单位显示名称，如果未找到则返回原代码
    """
    return UNIT_DISPLAY_MAP.get(unit_code, unit_code)


def get_unit_display_name_cn(unit_code):
    """获取单位中文显示名称（用于格式化显示）
    
    Args:
        unit_code (str): 单位代码
    
    Returns:
        str: 中文单位名称
    """
    unit_map_cn = {
        'kg': '公斤',
        'roll': '卷',
        'barrel': '桶',
        'box': '箱',
        'bag': '袋',
        'sqm': '㎡',
        'piece': '件',
    }
    return unit_map_cn.get(unit_code, unit_code)

