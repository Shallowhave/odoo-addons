# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import models


class QualityCheckWizard(models.TransientModel):
    _inherit = 'quality.check.wizard'

    # RFID 标签类型的质检向导现在通过前端组件处理
    # 前端组件会自动在质检对话框中显示 RFID 生成界面
    # 不需要额外的后端方法