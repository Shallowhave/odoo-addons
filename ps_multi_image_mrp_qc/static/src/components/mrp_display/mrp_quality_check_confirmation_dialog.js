/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MrpQualityCheckConfirmationDialog } from "@mrp_workorder/mrp_display/dialog/mrp_quality_check_confirmation_dialog";
import { TabletImageFieldMulti } from "./table_image_field"; // Adjust the path to TabletImageFieldMulti
import { useRef, onMounted, onWillUnmount } from "@odoo/owl";


patch(MrpQualityCheckConfirmationDialog.prototype, {
    setup() {
        super.setup();
        this.tabletImageFieldMultiRef = useRef("tabletImageFieldMulti"); // Create a reference to TabletImageFieldMulti

        // 监听多图片上传完成事件
        this._onMultipicAttachmentSaved = this._onMultipicAttachmentSaved.bind(this);

        // 在组件挂载时添加事件监听
        onMounted(() => {
            // Odoo 事件总线使用 on 方法
            if (this.env.bus && this.env.bus.on) {
                this.env.bus.on('multipic_attachment_saved', this, this._onMultipicAttachmentSaved);
            } else if (this.env.bus && this.env.bus.addEventListener) {
                this.env.bus.addEventListener('multipic_attachment_saved', this._onMultipicAttachmentSaved);
            }
        });

        // 在组件卸载时移除事件监听
        onWillUnmount(() => {
            if (this.env.bus && this.env.bus.off) {
                this.env.bus.off('multipic_attachment_saved', this, this._onMultipicAttachmentSaved);
            } else if (this.env.bus && this.env.bus.removeEventListener) {
                this.env.bus.removeEventListener('multipic_attachment_saved', this._onMultipicAttachmentSaved);
            }
        });
    },

    _onMultipicAttachmentSaved(eventData) {
        console.log('[对话框] 收到 multipic_attachment_saved 事件:', eventData);
        // 对于 multipic 类型，图片保存后不自动验证
        // 用户需要手动点击验证按钮来完成质检
        // 这样可以确保用户有足够时间检查上传的图片
        console.log('[对话框] 图片已保存，等待用户手动点击验证按钮');
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
