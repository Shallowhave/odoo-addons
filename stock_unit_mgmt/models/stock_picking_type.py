# -*- coding: utf-8 -*-

from odoo import models, fields, api


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    enable_enhanced_barcode_validation = fields.Boolean(
        string='启用增强条码验证',
        default=False,
        help='启用后，在条码应用中会执行增强的验证功能：\n'
             '- 验证扫码的批次号必须与预填的批次号匹配\n'
             '- 验证批次号列表是否一致\n'
             '- 验证数量是否一致\n'
             '- 检测重复扫描'
    )

