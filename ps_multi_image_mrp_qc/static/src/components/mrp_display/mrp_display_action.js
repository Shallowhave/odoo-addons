/** @odoo-module **/

import { useService } from "@web/core/utils/hooks";

export const SharedService = {
    notifyValidate() {
        // Trigger the validation event
        this.env.bus.trigger('custom_validate_event', {
            component: 'TabletImageFieldMulti',
            method: 'onSubmitButtonClick'
        });
    }
};
