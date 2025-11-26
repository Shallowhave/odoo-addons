# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mrp_auto_lot_batch_prefix = fields.Char(
        string='批次号前缀',
        config_parameter='mrp_auto_lot_generate.batch_prefix',
        default='XQ',
        help='自动生成批次号的前缀。默认值：XQ'
    )
    
    mrp_auto_lot_enable_logging = fields.Boolean(
        string='启用详细日志',
        config_parameter='mrp_auto_lot_generate.enable_logging',
        default=False,
        help='启用批次生成过程的详细日志记录'
    )
    
    mrp_auto_lot_override_generate_serial = fields.Boolean(
        string='覆盖原生批次号生成',
        config_parameter='mrp_auto_lot_generate.override_generate_serial',
        default=True,
        help='启用后，点击"创建新序列号/批号"按钮时将使用自定义批次号格式（如：XQ2511261421A10）。关闭后将使用 Odoo 原生的批次号生成方式。'
    )