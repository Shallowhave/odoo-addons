/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpLogNoteDialog } from "@mrp_workorder/mrp_display/dialog/mrp_log_note_dialog";

patch(MrpLogNoteDialog.prototype, {
    async _saveLogNote() {
        if (this.isProcess) {
            return false;
        }
        this.setButtonsDisabled(true);
        try {
            await this.props.record.save({ reload: false });
            if (this.props.reload) {
                await this.props.reload(this.props.record);
            }
            return true;
        } catch (error) {
            this.setButtonsDisabled(false);
            throw error;
        }
    },

    async _saveAndClose() {
        if (await this._saveLogNote()) {
            this.props.close();
        }
    },

    async _cancel() {
        await this._saveAndClose();
    },

    async _dismiss() {
        await this._saveLogNote();
    },
});
