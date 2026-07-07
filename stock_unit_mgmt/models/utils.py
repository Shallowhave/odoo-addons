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

COUNT_LIKE_UNIT_CODES = {'roll', 'barrel', 'box', 'bag', 'piece'}
CONTINUOUS_UNIT_CODES = {'kg', 'sqm'}

CONTINUOUS_CUSTOM_UNIT_EXACT = {
    'm',
    'meter',
    'meters',
    'metre',
    'metres',
    'kg',
    'kgs',
    'kilogram',
    'kilograms',
    't',
    'ton',
    'tons',
    'tonne',
    'tonnes',
    'sqm',
    'm2',
    'm²',
}

CONTINUOUS_CUSTOM_UNIT_TOKENS = (
    '米',
    '公尺',
    '毫米',
    '厘米',
    '㎡',
    '平方',
    '公斤',
    '千克',
    '吨',
)


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


def is_continuous_custom_unit(custom_unit_name):
    """Return whether a custom extra unit represents a continuous measure."""
    custom_name = (custom_unit_name or '').strip().lower().replace(' ', '')
    if not custom_name:
        return False
    return (
        custom_name in CONTINUOUS_CUSTOM_UNIT_EXACT
        or any(token in custom_name for token in CONTINUOUS_CUSTOM_UNIT_TOKENS)
    )


def should_default_quantity_to_one(unit_code, custom_unit_name=False):
    """Only count-like extra units should default to one."""
    if unit_code in COUNT_LIKE_UNIT_CODES:
        return True
    if unit_code in CONTINUOUS_UNIT_CODES:
        return False
    if unit_code == 'custom':
        custom_name = (custom_unit_name or '').strip()
        if not custom_name:
            return False
        return not is_continuous_custom_unit(custom_name)
    return False
