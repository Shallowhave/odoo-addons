# -*- coding: utf-8 -*-
from odoo import api, models


class QualityCheckWizard(models.TransientModel):
    _inherit = 'quality.check.wizard'

    def do_pass(self):
        """保持默认验证行为，不触发打印（改由独立按钮完成）。"""
        self.ensure_one()
        return super().do_pass()

    def action_print_label(self):
        """根据质检类型打印对应 PDF。

        - 当 test_type == 'product_label' 打印产品标签（action_report_mrp_label）
        - 当 test_type == 'qc_label' 打印质检标签（action_report_mrp_qc_label）
        - 其他类型不处理，返回 False
        """
        self.ensure_one()

        # 向导记录关联的质量检查记录（多重兜底）
        check = getattr(self, 'quality_check_id', False)
        if not check:
            ctx = self.env.context or {}
            active_model = ctx.get('active_model')
            active_id = ctx.get('active_id')
            if active_model == 'quality.check' and active_id:
                check = self.env['quality.check'].browse(active_id)
            elif active_model == 'quality.check.wizard' and active_id:
                wiz = self.browse(active_id)
                if wiz and getattr(wiz, 'quality_check_id', False):
                    check = wiz.quality_check_id
        if not check:
            return False

        # 仅支持工单（mo）模型打印
        production = False
        if check.production_id:
            production = check.production_id
        elif check.workorder_id and getattr(check.workorder_id, 'production_id', False):
            production = check.workorder_id.production_id

        if not production:
            return False

        # 根据测试类型选择动作（支持回退到质检点类型）
        derived_test_type = getattr(self, 'test_type', False)
        if not derived_test_type and check and getattr(check, 'point_id', False):
            tt = getattr(check.point_id, 'test_type_id', False)
            derived_test_type = tt and getattr(tt, 'technical_name', False) or False

        report_action_xmlid = False
        if derived_test_type == 'product_label':
            report_action_xmlid = 'xq_mrp_label.action_report_mrp_label'
        elif derived_test_type == 'qc_label':
            report_action_xmlid = 'xq_mrp_label.action_report_mrp_qc_label'

        if not report_action_xmlid:
            return False

        # 可选：强制 100x100 纸张
        ctx = dict(self.env.context or {})
        paper_xmlid = 'xq_mrp_label.paperformat_100x100'
        paper = self.env.ref(paper_xmlid, raise_if_not_found=False)
        if paper:
            ctx['force_paperformat_id'] = paper.id

        # 触发报表
        report_action = self.env.ref(report_action_xmlid, raise_if_not_found=False)
        return report_action.report_action(production, context=ctx)


