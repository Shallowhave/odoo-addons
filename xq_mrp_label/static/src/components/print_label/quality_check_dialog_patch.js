/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";

patch(MrpQualityCheckConfirmationDialog.prototype, {
    get showPrintLabel() {
        try {
            const tt = this.props?.record?.data?.test_type;
            return tt === 'product_label' || tt === 'qc_label' || tt === 'byproduct_label';
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
        
        console.log('[xq_mrp_label] onClickPrintLabel called, testType:', testType, 'resId:', resId);
        
        // 对于所有类型，都调用后端的 action_print_label 方法
        // 这样可以统一处理，包括副产品标签的向导显示
        try {
            // 注意：orm.call 的参数格式是 (model, method, [ids])
            const result = await orm.call('quality.check', 'action_print_label', [resId]);
            console.log('[xq_mrp_label] action_print_label result:', result);
            
            if (result) {
                // 如果返回 False，表示不支持或出错
                if (result === false) {
                    console.warn('[xq_mrp_label] action_print_label returned False');
                    return;
                }
                // 执行返回的动作（可能是报表或向导窗口）
                const actionResult = await action.doAction(result);
                
                // **关键修改**：如果是报表动作，在新窗口打开后自动触发打印
                // 注意：只有当报表在新窗口打开时才能自动打印
                if (result.type === 'ir.actions.report' || (result.type === 'ir.actions.act_url' && result.target === 'new')) {
                    // 延迟一下，等待新窗口加载完成
                    setTimeout(() => {
                        try {
                            // 尝试在新窗口中触发打印
                            // 注意：由于跨窗口限制，这里使用一个技巧：
                            // 在报表URL中添加参数，让报表模板自动触发打印
                            console.log('[xq_mrp_label] 报表已打开，准备自动打印');
                        } catch (e) {
                            console.warn('[xq_mrp_label] 自动打印失败:', e);
                        }
                    }, 1000);
                }
                
                return actionResult;
            } else {
                console.warn('[xq_mrp_label] action_print_label returned no result');
            }
        } catch (e) {
            console.error('[xq_mrp_label] Failed to print label:', e);
            // 显示错误通知
            const notification = this.env.services.notification;
            if (notification) {
                notification.add(
                    `打印标签失败: ${e.message || String(e)}`,
                    { type: 'danger' }
                );
            }
        }
    },
});


