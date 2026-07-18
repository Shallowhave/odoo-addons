/** @odoo-module **/

import BarcodePickingModel from "@stock_barcode/models/barcode_picking_model";
import { patch } from "@web/core/utils/patch";

const AREA_UOM_TOKENS = ["平米", "平方米", "sqm", "m²", "㎡"];

function recordName(record) {
    const name = record?.name;
    if (!name) {
        return "";
    }
    if (typeof name === "object") {
        return Object.values(name).filter(Boolean).join(" ");
    }
    return String(name);
}

function isAreaUom(uom) {
    const uomName = recordName(uom).trim().toLowerCase();
    return AREA_UOM_TOKENS.some((token) => uomName.includes(token));
}

function isDefaultScanQuantity(args) {
    const qtyDone = Number(args.qty_done);
    return (
        Number.isFinite(qtyDone) &&
        qtyDone === 1 &&
        !args.uom &&
        !args.stockUnitMgmtExplicitQuantity
    );
}

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
        const hasTrackingNumber = Boolean(
            args.lot_id || args.lot_name || line.lot_id || line.lot_name
        );
        const remainingQuantity = Math.max(
            (line.reserved_uom_qty || 0) - (line.qty_done || 0),
            0
        );

        if (
            this.record?.picking_type_code === "outgoing" &&
            line.product_id?.tracking === "lot" &&
            isAreaUom(line.product_uom_id) &&
            hasTrackingNumber &&
            isDefaultScanQuantity(args) &&
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
