/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { ComponentScanWizard } from "./component_scan_wizard";

patch(MrpQualityCheckConfirmationDialog.prototype, {
    setup() {
        super.setup();
        this.orm = this.env.services.orm;
        // 存储 ComponentScanWizard 实例的引用
        this.componentScanWizardInstance = null;
        // 在 setup 中创建 registerInstance 函数，确保 this 上下文正确
        this.registerComponentScanWizard = (instance) => {
            console.log('[对话框] registerComponentScanWizard 被调用，实例:', instance);
            this.componentScanWizardInstance = instance;
        };
    },

    get componentScanInfo() {
        return {
            name: "component_scan_verify",
            record: this.props.record,
            close: this.props.close,
            validate: this.validate && this.validate.bind(this),
            // 使用 setup 中创建的 registerInstance 函数
            registerInstance: this.registerComponentScanWizard,
        };
    },
    
    async validate() {
        console.log('[对话框 validate] 被调用');
        // 如果是组件扫码确认类型，先让 ComponentScanWizard 处理验证
        const recordData = this.props.record.data;
        console.log('[对话框 validate] test_type:', recordData.test_type);
        console.log('[对话框 validate] componentScanWizardInstance:', this.componentScanWizardInstance);
        
        if (recordData.test_type === 'component_scan_verify') {
            // 通过存储的实例引用调用 onValidate
            if (this.componentScanWizardInstance && typeof this.componentScanWizardInstance.onValidate === 'function') {
                console.log('[对话框 validate] 调用 ComponentScanWizard.onValidate');
                const result = await this.componentScanWizardInstance.onValidate();
                console.log('[对话框 validate] ComponentScanWizard.onValidate 返回:', result);
                // 如果 onValidate 返回 false，说明验证失败，直接返回（不调用父类）
                if (result === false) {
                    console.log('[对话框 validate] 验证失败，不继续');
                    return; // 直接返回，不调用父类
                }
                // 如果 onValidate 返回 true，说明验证成功，继续调用父类的 validate
                console.log('[对话框 validate] 验证成功，继续调用父类 validate');
            } else {
                console.warn('[对话框 validate] ComponentScanWizard 实例不存在或 onValidate 方法不可用');
                // 如果实例不存在，也继续调用父类方法（让父类处理）
            }
        }
        
        // 调用父类的 validate 方法
        // 注意：不要使用 await 和存储返回值，直接返回父类的调用结果
        console.log('[对话框 validate] 调用父类 validate');
        return super.validate(...arguments);
    },
});

MrpQualityCheckConfirmationDialog.components = { 
    ...MrpQualityCheckConfirmationDialog.components, 
    ComponentScanWizard
};

