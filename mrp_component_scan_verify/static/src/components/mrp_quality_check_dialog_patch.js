/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { ComponentScanWizard } from "./component_scan_wizard";

function runSingleFlight(target, key, operation) {
    if (target[key]) {
        return target[key];
    }
    const operationPromise = Promise.resolve().then(operation);
    const guardedPromise = operationPromise.finally(() => {
        if (target[key] === guardedPromise) {
            delete target[key];
        }
    });
    target[key] = guardedPromise;
    return guardedPromise;
}

patch(ComponentScanWizard.prototype, {
    onSelectComponent() {
        const args = arguments;
        return runSingleFlight(this, "_componentSelectionPromise", () =>
            super.onSelectComponent(...args)
        );
    },

    processBarcode() {
        const args = arguments;
        if (args[0]) {
            this.state.isScanning = true;
        }
        return runSingleFlight(this, "_barcodeProcessingPromise", () =>
            super.processBarcode(...args)
        );
    },
});

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
    
    validate() {
        const args = arguments;
        this.state.disabled = true;
        return runSingleFlight(this, "_qualityValidationPromise", async () => {
            try {
                const recordData = this.props.record.data;
                if (
                    recordData.test_type === "component_scan_verify" &&
                    this.componentScanWizardInstance &&
                    typeof this.componentScanWizardInstance.onValidate === "function"
                ) {
                    const result = await this.componentScanWizardInstance.onValidate();
                    if (result === false) {
                        this.state.disabled = false;
                        return;
                    }
                }
                return await super.validate(...args);
            } catch (error) {
                this.state.disabled = false;
                throw error;
            }
        });
    },
});

MrpQualityCheckConfirmationDialog.components = { 
    ...MrpQualityCheckConfirmationDialog.components, 
    ComponentScanWizard
};
