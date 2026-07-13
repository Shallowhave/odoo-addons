/** @odoo-module **/

import BarcodePickingModel from "@stock_barcode/models/barcode_picking_model";
import { patch } from "@web/core/utils/patch";

const AREA_UOM_TOKENS = ["平米", "平方米", "sqm", "m²", "㎡"];

patch(BarcodePickingModel.prototype, {
    async _processGs1Data(data, filters) {
        const result = await super._processGs1Data(...arguments);
        if (data.type === "quantity" && result.quantity !== undefined) {
            result.stockUnitMgmtExplicitQuantity = true;
        }
        return result;
    },

    _convertDataToFieldsParams(args) {
        const params = super._convertDataToFieldsParams(...arguments);
        if (args.stockUnitMgmtExplicitQuantity) {
            params.stockUnitMgmtExplicitQuantity = true;
        }
        return params;
    },

    _updateLineQty(line, args) {
        const uomName = (line.product_uom_id?.name || "").trim().toLowerCase();
        const isAreaUom = AREA_UOM_TOKENS.some((token) => uomName.includes(token));
        const isLotScan = Boolean(args.lot_id || args.lot_name);
        const isDefaultScanQuantity =
            args.qty_done === 1 &&
            !args.uom &&
            !args.stockUnitMgmtExplicitQuantity;
        const remainingQuantity = Math.max(
            (line.reserved_uom_qty || 0) - (line.qty_done || 0),
            0
        );

        if (
            line.product_id?.tracking === "lot" &&
            isAreaUom &&
            isLotScan &&
            isDefaultScanQuantity &&
            remainingQuantity > 1
        ) {
            return super._updateLineQty(line, {
                ...args,
                qty_done: remainingQuantity,
            });
        }
        return super._updateLineQty(...arguments);
    },
});
