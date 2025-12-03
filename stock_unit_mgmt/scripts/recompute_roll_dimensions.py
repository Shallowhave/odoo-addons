#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动触发原膜产品的实际米数和实际平方重新计算
"""
import sys
import os

# 添加 Odoo 路径
sys.path.insert(0, '/usr/lib/python3/dist-packages')

import odoo
from odoo import api, SUPERUSER_ID

if __name__ == '__main__':
    # 初始化 Odoo
    odoo.tools.config.parse_config(['-c', '/etc/odoo/odoo.conf'])
    dbname = 'XQ003'
    
    with odoo.api.Environment.manage():
        env = api.Environment(odoo.registry(dbname), SUPERUSER_ID, {})
        
        # 查找所有原膜产品的库存记录
        quants = env['stock.quant'].search([
            ('product_id.product_tmpl_id.default_unit_config', '=', 'roll'),
            ('quantity', '>', 0)
        ])
        
        print(f"找到 {len(quants)} 条原膜产品库存记录")
        
        # 触发重新计算
        quants.invalidate_recordset(['actual_length_m', 'actual_area_sqm'])
        
        # 读取字段以触发计算
        for quant in quants:
            actual_length = quant.actual_length_m
            actual_area = quant.actual_area_sqm
            print(f"ID={quant.id}, 产品={quant.product_id.name}, 数量={quant.quantity}, "
                  f"实际米数={actual_length}, 实际平方={actual_area}")
        
        env.cr.commit()
        print("重新计算完成")

