/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";

patch(MrpQualityCheckConfirmationDialog.prototype, {
    get showPrintLabel() {
        try {
            const tt = this.props?.record?.data?.test_type;
            return tt === 'product_label' || tt === 'qc_label';
        } catch (e) {
            return false;
        }
    },
    async onClickPrintLabel() {
        const record = this.props.record;
        const resId = record?.data?.id;
        const orm = this.env.services.orm;
        const action = this.env.services.action;
        if (!resId) {
            console.warn('[xq_mrp_label] No record id on dialog record');
            return;
        }

        // 直接基于前端数据判断打印哪张标签
        const testType = (record && record.data && record.data.test_type) || null;
        const actionInfo = testType === 'product_label'
            ? { report_name: 'xq_mrp_label.mrp_label_document' }
            : (testType === 'qc_label' ? { report_name: 'xq_mrp_label.mrp_qc_label_document' } : null);
        if (!actionInfo) {
            console.warn('[xq_mrp_label] Unsupported test_type:', testType);
            return;
        }

        // 读取 production_id（必要时从工序回溯）
        try {
            let prodId = null;
            const checks = await orm.read('quality.check', [resId], ['production_id','workorder_id']);
            if (checks && checks[0]) {
                const c = checks[0];
                if (c.production_id && c.production_id[0]) {
                    prodId = c.production_id[0];
                } else if (c.workorder_id && c.workorder_id[0]) {
                    const wos = await orm.read('mrp.workorder', [c.workorder_id[0]], ['production_id']);
                    if (wos && wos[0] && wos[0].production_id && wos[0].production_id[0]) {
                        prodId = wos[0].production_id[0];
                    }
                }
            }
            if (!prodId) {
                console.error('[xq_mrp_label] Cannot resolve production_id for check', resId);
                return;
            }
            const ctx = { active_model: 'mrp.production', active_ids: [prodId], active_id: prodId };
            const act = {
                type: 'ir.actions.report',
                report_type: 'qweb-pdf',
                report_name: actionInfo.report_name,
                report_file: actionInfo.report_name,
                context: ctx,
            };
            return action.doAction(act);
        } catch (e) {
            console.error('[xq_mrp_label] action.doAction(report) failed', e);
        }
    },
});


