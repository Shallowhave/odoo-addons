# -*- coding: utf-8 -*-

from odoo import fields, models, api


class QualityPoint(models.Model):
    _inherit = 'quality.point'
    
    # 注意：pad设备自带扫码功能，不需要配置额外设备
    # 扫码功能通过浏览器的条码扫描API或直接输入条码实现

