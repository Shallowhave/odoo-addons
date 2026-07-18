# -*- coding: utf-8 -*-
from odoo import fields, models

class RfidTag(models.Model):
    _inherit = "rfid.tag"
    
    tid = fields.Char(string="TID", size=24, help="物理标签的 TID 标识 (Hex)")
    write_count = fields.Integer(string="写入次数", default=0, help="记录此标签被写入的次数")
