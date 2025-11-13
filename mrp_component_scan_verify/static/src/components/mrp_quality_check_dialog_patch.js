/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { ComponentScanWizard } from "./component_scan_wizard";

patch(MrpQualityCheckConfirmationDialog.prototype, {
    setup() {
        super.setup();
        this.orm = this.env.services.orm;
    },

    get componentScanInfo() {
        return {
            name: "component_scan_verify",
            record: this.props.record,
            close: this.props.close,
            validate: this.validate && this.validate.bind(this),
        };
    },
});

MrpQualityCheckConfirmationDialog.components = { 
    ...MrpQualityCheckConfirmationDialog.components, 
    ComponentScanWizard
};

