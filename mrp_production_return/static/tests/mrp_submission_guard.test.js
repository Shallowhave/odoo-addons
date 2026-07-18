import { describe, expect, test } from "@odoo/hoot";
import { Deferred } from "@odoo/hoot-mock";
import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { MrpRegisterProductionDialog } from "@mrp_workorder/mrp_display/dialog/mrp_register_production_dialog";
import "@mrp_production_return/js/mrp_submission_guard_patch";

describe("MRP submission guards", () => {
    test("coalesces concurrent work order pre-validation", async () => {
        const saveFinished = new Deferred();
        let saveCount = 0;
        let preValidationCount = 0;
        const record = {
            resId: 42,
            resModel: "mrp.workorder",
            data: { employee_ids: { records: [] } },
            save() {
                saveCount++;
                return saveFinished;
            },
        };
        const component = {
            model: {
                orm: {
                    async call(_model, method) {
                        if (method === "pre_record_production") {
                            preValidationCount++;
                        }
                        return true;
                    },
                },
            },
            props: {
                addToValidationStack() {},
                record,
                sessionOwner: { id: false },
            },
            record: { qty_producing: 1, state: "progress" },
            state: { underValidation: false, validated: false },
        };

        const first = MrpDisplayRecord.prototype.validate.call(component);
        const second = MrpDisplayRecord.prototype.validate.call(component);
        saveFinished.resolve();
        await Promise.all([first, second]);

        expect(saveCount).toBe(1);
        expect(preValidationCount).toBe(1);
    });

    test("coalesces concurrent work order validation", async () => {
        const validationFinished = new Deferred();
        let validationCount = 0;
        const component = {
            resModel: "mrp.workorder",
            state: { validated: false },
            workorderValidation() {
                validationCount++;
                return validationFinished;
            },
        };

        const first = MrpDisplayRecord.prototype.realValidation.call(component);
        const second = MrpDisplayRecord.prototype.realValidation.call(component);
        validationFinished.resolve();
        await Promise.all([first, second]);

        expect(validationCount).toBe(1);
    });

    test("coalesces concurrent direct work order finishing", async () => {
        const finishCompleted = new Deferred();
        let finishCount = 0;
        const component = {
            env: { reload() {} },
            model: {
                orm: {
                    call() {
                        finishCount++;
                        return finishCompleted;
                    },
                },
            },
            props: {
                production: { data: { product_qty: 1 } },
                record: { resId: 42, resModel: "mrp.workorder" },
                async removeFromValidationStack() {},
            },
            state: { validated: false },
            trackingMode: "none",
        };

        const first = MrpDisplayRecord.prototype.workorderValidation.call(component);
        const second = MrpDisplayRecord.prototype.workorderValidation.call(component, true);
        finishCompleted.resolve();
        await Promise.all([first, second]);

        expect(finishCount).toBe(1);
    });

    test("coalesces concurrent close production requests", async () => {
        const workorderFinished = new Deferred();
        let actionCount = 0;
        let finishCount = 0;
        let removeCount = 0;
        const component = {
            model: {
                orm: {
                    async call() {
                        actionCount++;
                        return { context: {} };
                    },
                },
            },
            props: {
                production: { resId: 7 },
                record: { resId: 42 },
                async removeFromValidationStack() {
                    removeCount++;
                },
            },
            trackingMode: "none",
            workorderValidation() {
                finishCount++;
                return workorderFinished;
            },
            _doAction() {},
        };

        const first = MrpDisplayRecord.prototype.onClickCloseProduction.call(component);
        const second = MrpDisplayRecord.prototype.onClickCloseProduction.call(component);
        await Promise.resolve();
        workorderFinished.resolve();
        await Promise.all([first, second]);

        expect(removeCount).toBe(1);
        expect(finishCount).toBe(1);
        expect(actionCount).toBe(1);
    });

    test("coalesces concurrent quick production registration", async () => {
        const updateFinished = new Deferred();
        let updateCount = 0;
        let setQuantityCount = 0;
        const production = {
            data: { product_qty: 10 },
            resIds: [7],
            update() {
                updateCount++;
                return updateFinished;
            },
            model: {
                orm: {
                    async call(_model, method) {
                        if (method === "set_qty_producing") {
                            setQuantityCount++;
                        }
                    },
                },
            },
        };
        const component = {
            productionComplete: false,
            props: { production },
            env: { async reload() {} },
        };

        const first = MrpDisplayRecord.prototype.quickRegisterProduction.call(component);
        const second = MrpDisplayRecord.prototype.quickRegisterProduction.call(component);
        updateFinished.resolve();
        await Promise.all([first, second]);

        expect(updateCount).toBe(1);
        expect(setQuantityCount).toBe(1);
    });

    test("coalesces concurrent register production dialog validation", async () => {
        const validationFinished = new Deferred();
        let validationCount = 0;
        const dialog = {
            componentScanWizardInstance: null,
            props: { record: { data: { test_type: false } } },
            recordData: { test_type: false },
            state: { disabled: false },
            doActionAndClose() {
                validationCount++;
                return validationFinished;
            },
        };

        const first = MrpRegisterProductionDialog.prototype.validate.call(dialog);
        const second = MrpRegisterProductionDialog.prototype.validate.call(dialog);
        validationFinished.resolve();
        await Promise.all([first, second]);

        expect(validationCount).toBe(1);
    });

    test("allows register production validation retry after failure", async () => {
        let validationCount = 0;
        let shouldFail = true;
        const dialog = {
            recordData: { test_type: false },
            state: { disabled: false },
            doActionAndClose() {
                validationCount++;
                if (shouldFail) {
                    return Promise.reject(new Error("validation failed"));
                }
                return Promise.resolve();
            },
        };

        let validationError;
        try {
            await MrpRegisterProductionDialog.prototype.validate.call(dialog);
        } catch (error) {
            validationError = error;
        }

        expect(validationError.message).toBe("validation failed");
        expect(dialog.state.disabled).toBe(false);

        shouldFail = false;
        await MrpRegisterProductionDialog.prototype.validate.call(dialog);
        expect(validationCount).toBe(2);
    });
});
