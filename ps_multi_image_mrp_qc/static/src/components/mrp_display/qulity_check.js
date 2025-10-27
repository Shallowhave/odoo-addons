/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { QualityCheck } from "@mrp_workorder/mrp_display/mrp_record_line/quality_check";

patch(QualityCheck.prototype, {
    get icon() {
        switch (this.props.record.data.test_type) {
            case "picture":
                return "fa fa-camera";
            case "multipic":
                return "fa fa-camera";
            case "register_consumed_materials":
            case "register_byproduct":
                return "fa fa-barcode";
            case "instructions":
                return "fa fa-square-o";
            case "passfail":
                return "fa fa-check";
            case "measure":
                return "fa fa-arrows-h";
            case "print_label":
                return "fa fa-print";
            default:
                return "fa fa-lightbulb-o";
        }
    }
});