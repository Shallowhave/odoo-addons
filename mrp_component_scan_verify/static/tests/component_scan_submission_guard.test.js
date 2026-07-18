import { describe, expect, test } from "@odoo/hoot";
import { Deferred } from "@odoo/hoot-mock";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { ComponentScanWizard } from "@mrp_component_scan_verify/components/component_scan_wizard";
import "@mrp_component_scan_verify/components/mrp_quality_check_dialog_patch";

describe("Component scan submission guards", () => {
    test("coalesces concurrent component selection writes", async () => {
        const selectionWriteFinished = new Deferred();
        let selectionWriteCount = 0;
        const wizard = {
            notification: { add() {} },
            orm: {
                write() {
                    selectionWriteCount++;
                    return selectionWriteFinished;
                },
            },
            props: { record: { data: { id: 99 } } },
            state: {
                requiredComponents: [
                    {
                        key: "move-line:7",
                        id: 11,
                        name: "Component A",
                        code: "COMP-A",
                        move_id: 5,
                        move_line_id: 7,
                        lot_id: 13,
                        lot_name: "LOT-001",
                    },
                ],
                scannedProduct: null,
            },
        };

        const first = ComponentScanWizard.prototype.onSelectComponent.call(
            wizard,
            "move-line:7"
        );
        const second = ComponentScanWizard.prototype.onSelectComponent.call(
            wizard,
            "move-line:7"
        );
        selectionWriteFinished.resolve();
        await Promise.all([first, second]);

        expect(selectionWriteCount).toBe(1);
    });

    test("coalesces concurrent processing of the same barcode", async () => {
        const productSearchFinished = new Deferred();
        let productSearchCount = 0;
        const wizard = {
            notification: { add() {} },
            orm: {
                async searchRead(model) {
                    if (model === "product.product") {
                        productSearchCount++;
                        await productSearchFinished;
                    }
                    return [];
                },
            },
            state: {
                isScanning: false,
                scannedBarcode: "",
                scannedLotId: null,
                scannedProduct: null,
                scannedProductCode: "",
                scannedProductName: "",
                verificationMessage: "",
                verificationResult: "pending",
            },
        };

        const first = ComponentScanWizard.prototype.processBarcode.call(wizard, "LOT-001");
        const second = ComponentScanWizard.prototype.processBarcode.call(wizard, "LOT-001");
        productSearchFinished.resolve();
        await Promise.all([first, second]);

        expect(productSearchCount).toBe(1);
    });

    test("coalesces concurrent component quality validation", async () => {
        const componentValidationFinished = new Deferred();
        let componentValidationCount = 0;
        const dialog = {
            componentScanWizardInstance: {
                onValidate() {
                    componentValidationCount++;
                    return componentValidationFinished;
                },
            },
            props: { record: { data: { test_type: "component_scan_verify" } } },
            state: { disabled: false },
        };

        const first = MrpQualityCheckConfirmationDialog.prototype.validate.call(dialog);
        const second = MrpQualityCheckConfirmationDialog.prototype.validate.call(dialog);
        componentValidationFinished.resolve(false);
        await Promise.all([first, second]);

        expect(componentValidationCount).toBe(1);
        expect(dialog.state.disabled).toBe(false);

        await MrpQualityCheckConfirmationDialog.prototype.validate.call(dialog);
        expect(componentValidationCount).toBe(2);
    });
});
