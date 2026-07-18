/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { MrpRegisterProductionDialog } from "@mrp_workorder/mrp_display/dialog/mrp_register_production_dialog";

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

patch(MrpDisplayRecord.prototype, {
    validate() {
        const args = arguments;
        return runSingleFlight(this, "_xqPreValidationPromise", () =>
            super.validate(...args)
        );
    },

    realValidation() {
        const args = arguments;
        return runSingleFlight(this, "_xqRecordValidationPromise", () =>
            super.realValidation(...args)
        );
    },

    workorderValidation() {
        const args = arguments;
        return runSingleFlight(this, "_xqWorkorderFinishPromise", () =>
            super.workorderValidation(...args)
        );
    },

    onClickCloseProduction() {
        const args = arguments;
        return runSingleFlight(this, "_xqCloseProductionPromise", () =>
            super.onClickCloseProduction(...args)
        );
    },

    quickRegisterProduction() {
        const args = arguments;
        return runSingleFlight(this, "_xqQuickProductionPromise", () =>
            super.quickRegisterProduction(...args)
        );
    },
});

patch(MrpRegisterProductionDialog.prototype, {
    validate() {
        const args = arguments;
        this.state.disabled = true;
        return runSingleFlight(this, "_xqDialogValidationPromise", async () => {
            try {
                return await super.validate(...args);
            } catch (error) {
                this.state.disabled = false;
                throw error;
            }
        });
    },
});
