/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { RfidGenerationWizard } from "./rfid_generation_wizard";

patch(MrpQualityCheckConfirmationDialog.prototype, {
    setup() {
        super.setup();
        this.orm = this.env.services.orm;
    },

    get rfidInfo() {
        return {
            name: "rfid_label",
            record: this.props.record,
            close: this.props.close,
            validate: this.validate && this.validate.bind(this),
        };
    },
});

MrpQualityCheckConfirmationDialog.components = { 
    ...MrpQualityCheckConfirmationDialog.components, 
    RfidGenerationWizard 
};

console.log('RFID components loaded:', MrpQualityCheckConfirmationDialog.components);
