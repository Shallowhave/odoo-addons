/** @odoo-module **/

// 暂时注释掉 JavaScript 代码，避免导致页面无法加载
// 先使用 XML 视图中的 no_create 选项和自定义按钮来实现功能

/*
import { patch } from "@web/core/utils/patch";
import { Many2oneField } from "@web/views/fields/many2one/many2one_field";
import { useService } from "@web/core/utils/hooks";

patch(Many2oneField.prototype, {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
    },

    async onAdd(ev) {
        if (this.props.name === 'lot_producing_id') {
            const record = this.props.record;
            const productionId = record.resId;
            
            if (!productionId || record.model !== 'mrp.production') {
                return super.onAdd(...arguments);
            }
            
            const productTracking = record.data.product_id?.data?.tracking;
            if (!productTracking || !['lot', 'serial'].includes(productTracking)) {
                return super.onAdd(...arguments);
            }
            
            if (record.data.lot_producing_id) {
                return super.onAdd(...arguments);
            }
            
            const options = this.props.options || {};
            if (options.no_create) {
                try {
                    await this.orm.call(
                        "mrp.production",
                        "action_create_lot_producing",
                        [productionId]
                    );
                    
                    await record.load();
                    
                    this.notification.add(
                        "批次号已自动生成",
                        { type: "success" }
                    );
                    return;
                } catch (error) {
                    console.error("自动生成批次号失败:", error);
                    this.notification.add(
                        error.message || "生成批次号失败",
                        { type: "danger" }
                    );
                    return;
                }
            }
        }
        
        return super.onAdd(...arguments);
    },
});
*/

