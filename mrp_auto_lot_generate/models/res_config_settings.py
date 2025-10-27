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
