/** @odoo-module **/

import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ImageField, imageField } from '@web/views/fields/image/image_field';
import { post } from "@web/core/network/http_service"; // Import post method for HTTP requests
import { Component, useState, onWillStart } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { rpc } from "@web/core/network/rpc";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";


export class TabletImageFieldMulti extends ConfirmationDialog {

    static props = {
        ...ConfirmationDialog.props,
        name: { type: String, optional: true },
        width: { type: Number, optional: true },
        height: { type: Number, optional: true },
        record: Object,
        close: { type: Function, optional: true },
        }

    setup() {
        super.setup();
        this.dialog = useService("dialog");
        this.attachments = []; // Initialize attachments array to store uploaded attachments
        this.selectedImages = []; // Initialize array to store selected image files
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            message: '',
            attachments: [],
            isButtonClicked: true,
        });
        this.resId = this.props.resId || this.props.record.data.id;  // Assuming record.data.id contains the resId
        this.resModel = this.props.resModel || this.props.record.model;  // Assuming record.model contains the resModel


    onWillStart(async () => {
        this.fetchExistingAttachments();
        })
    }

    async fetchExistingAttachments() {
        try {
            const attachments = await rpc('/custom/attachment/get', {
                res_id: this.resId,
            });
            this.state.attachments = attachments;
        } catch (error) {
            this.notification.add(
                _t("Could not fetch existing attachments."),
                { type: 'danger', sticky: true }
            );
        }
    }

    mounted() {
        // Add the event listener to the form in the mounted hook
        this.form = document.querySelector('#multi_image_form');
        if (this.form) {
            this.form.addEventListener('submit', this.onSubmitButtonClick.bind(this));
        }
    }

    async onFileInputChange(event) {
    const files = event.target.files;
    const newAttachments = [];

    for (const file of files) {

        // Create a temporary attachment object
        const temporaryAttachment = {
            id: new Date().getTime() + file.lastModified, // Unique ID for temporary attachments
            name: file.name,
            file: file,
            state: 'temporary',
        };
        newAttachments.push(temporaryAttachment);
    }
    console.log('New attachments:', newAttachments);
    console.log('Current attachments:', this.state.attachments);

    this.state.attachments.push(...newAttachments);

    if (this.state.attachments.length > 0) {
        this.state.isButtonClicked = false; // Hide the button
        }

    event.target.value = null;
}

    async onFileInputChange1(event) {
    const files = event.target.files;
    const csrf_token = odoo.csrf_token || document.querySelector('input[name="csrf_token"]').value; // Ensure CSRF token is fetched correctly
    const newAttachments = [];

    for (const file of files) {
        try {
            const data = new FormData();
            data.append('name', file.name);
            data.append('file', file);
            data.append('res_id', this.resId);
            data.append('res_model', 'quality.check');
            data.append('csrf_token', csrf_token);  // Ensure CSRF token is included

            const response = await fetch('/portal/attachment/add', {
                method: 'POST',
                body: data,
            });

            if (!response.ok) {
                throw new Error(`Error: ${response.statusText}`);
            }

            const attachment = await response.json();
            newAttachments.push({
                ...attachment,
                state: 'pending',
            });
        } catch (error) {
            this.notification.add(
                _t("Could not save file <strong>%s</strong>", escape(file.name)),
                { type: 'warning', sticky: true }
            );
        }
    }

    this.state.attachments.push(...newAttachments);
    event.target.value = null;
}

    async deleteAttachment(event) {
    const attachmentId = parseInt(event.currentTarget.closest('.o_portal_chatter_attachment').dataset.id);
    const attachment = this.state.attachments.find(att => att.id === attachmentId);

    if (!attachment) {
        return;
    }

    // Remove the attachment from the state immediately
    this.state.attachments = this.state.attachments.filter(att => att.id !== attachmentId);

    if (attachment.state === 'temporary') {
        // No server-side action needed for temporary attachments
        console.log('Temporary attachment deleted:', attachment.name);
        return;
    }

    // Handle server-side deletion for permanent attachments
    try {
        const response = await rpc('/custom/attachment/remove', {
            attachment_id: attachmentId,
            res_id: this.resId,
        });

        if (!response.success) {
            throw new Error('Deletion failed');
        }

        console.log('Attachment Permanently successfully deleted:', attachment.name);
    } catch (error) {
        this.notification.add(
            _t("Could not delete attachment <strong>%s</strong>", escape(attachment.name)),
            { type: 'warning', sticky: true }
        );
        // Re-add the attachment back to the state if deletion fails
        this.state.attachments.push(attachment);
    }
}

    triggerFileInput() {
        document.querySelector('.o_portal_chatter_file_input').click();
    }

    prepareMessageData() {
        return {
            message: this.state.message,
            attachment_ids: this.state.attachments.map(a => a.id),
            attachment_tokens: this.state.attachments.map(a => a.access_token),
        };
    }

    async onSubmitButtonClick(event) {
    this.state.isButtonClicked = true;

    event.preventDefault();
    const error = this.checkContent();
    if (error) {
        document.querySelector(".o_portal_chatter_composer_error").textContent = error;
        document.querySelector(".o_portal_chatter_composer_error").classList.remove('d-none');
        return;
    }

    try {
        const csrf_token = odoo.csrf_token || document.querySelector('input[name="csrf_token"]').value;
        const attachments = [];

        for (const attachment of this.state.attachments) {
            if (attachment.state === 'temporary') {
                const data = new FormData();
                data.append('name', attachment.name);
                data.append('file', attachment.file);
                data.append('res_id', this.resId);
                data.append('res_model', 'quality.check');
                data.append('csrf_token', csrf_token);

                const response = await fetch('/portal/attachment/add', {
                    method: 'POST',
                    body: data,
                });

                if (!response.ok) {
                    throw new Error(`Error: ${response.statusText}`);
                }

                const savedAttachment = await response.json();
                attachments.push(savedAttachment);
            } else {
                attachments.push(attachment);
            }
        }

        const result = await rpc('/custom/attachment/add', {
            res_id: this.resId,
            attachments: attachments,
            message: this.state.message
        });

        if (result.success) {
            console.log('Attachment successfully saved:', result);
            this.env.bus.trigger('reload_chatter_content', result);
            window.location.reload();
        } else {
            this.notification.add(
                _t(result.message || "Error while sending the message."),
                { type: 'danger', sticky: true }
            );
        }

    } catch (error) {
        this.notification.add(
            _t("Error while sending the message."),
            { type: 'danger', sticky: true }
        );
    }

}


    checkContent() {
        if (!this.state.attachments.length) {
            return _t('Some fields are required. Please make sure to attach a document');
        }
        return null;
    }

}

TabletImageFieldMulti.template = "ps_multi_image_mrp_qc.ForMultiImages";

export const tabletImageFieldMulti = {
    ...imageField,
    component: TabletImageFieldMulti,
};

registry.category("fields").add("multi_picture", tabletImageFieldMulti);
