# -*- coding: utf-8 -*-
from odoo import models


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    def action_print_label(self):
        self.ensure_one()

        production = False
        if self.production_id:
            production = self.production_id
        elif self.workorder_id and getattr(self.workorder_id, 'production_id', False):
            production = self.workorder_id.production_id

        if not production:
            return False

        # 从质检点类型判断打印哪张标签
        test_type = False
        if getattr(self, 'test_type', False):
            test_type = self.test_type
        if not test_type and self.point_id and self.point_id.test_type_id:
            test_type = getattr(self.point_id.test_type_id, 'technical_name', False)

        if test_type == 'product_label':
            xmlid = 'xq_mrp_label.action_report_mrp_label'
        elif test_type == 'qc_label':
            xmlid = 'xq_mrp_label.action_report_mrp_qc_label'
        else:
            return False

        ctx = dict(self.env.context or {})
        paper = self.env.ref('xq_mrp_label.paperformat_100x100', raise_if_not_found=False)
        if paper:
            ctx['force_paperformat_id'] = paper.id

        action = self.env.ref(xmlid)
        return action.report_action(production, context=ctx)


