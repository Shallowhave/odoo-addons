/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { TabletImageFieldMulti } from "./table_image_field"; // Adjust the path to TabletImageFieldMulti
import { useRef } from "@odoo/owl";


patch(MrpQualityCheckConfirmationDialog.prototype, {
    setup() {
        super.setup();
        this.tabletImageFieldMultiRef = useRef("tabletImageFieldMulti"); // Create a reference to TabletImageFieldMulti
    },

    get multipicInfo() {
        return {
            name: "multipic",
            record: this.props.record,
            width: 80,
            height: 80,
        };
    }
});

MrpQualityCheckConfirmationDialog.components = { ...MrpQualityCheckConfirmationDialog.components, TabletImageFieldMulti };

console.log('Available components:', MrpQualityCheckConfirmationDialog.components);
