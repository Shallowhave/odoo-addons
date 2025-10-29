/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Field } from "@web/views/fields/field";
import { registry } from "@web/core/registry";

// 补丁Field组件来动态过滤单位选项
patch(Field.prototype, {
    setup() {
        super.setup();
        if (this.props.name === 'lot_unit_name' && this.props.record) {
            this._filterUnitOptions();
        }
    },
    
    async _filterUnitOptions() {
        const record = this.props.record;
        const productId = record.data.product_id?.[0];
        
        if (!productId) {
            return;
        }
        
        try {
            // 获取产品配置
            const product = await this.env.services.orm.read('product.product', [productId], ['product_tmpl_id']);
            if (!product || !product.length) return;
            
            const productTmpl = await this.env.services.orm.read(
                'product.template',
                [product[0].product_tmpl_id[0]],
                ['default_unit_config', 'enable_custom_units', 'quick_unit_name']
            );
            
            if (!productTmpl || !productTmpl.length) return;
            const config = productTmpl[0];
            
            if (!config.enable_custom_units || !config.default_unit_config) {
                return;
            }
            
            // 等待字段渲染完成
            await this.env.services.ui.awaitNextTick();
            
            // 查找选择字段的DOM元素
            const fieldEl = document.querySelector(`[data-name="lot_unit_name"]`);
            if (!fieldEl) return;
            
            const selectEl = fieldEl.querySelector('select');
            if (!selectEl) return;
            
            // 确定允许的值
            const allowedValue = config.default_unit_config === 'custom' ? 'custom' : config.default_unit_config;
            
            // 移除不允许的选项
            Array.from(selectEl.options).forEach(option => {
                if (option.value && option.value !== allowedValue) {
                    option.style.display = 'none';
                }
            });
            
            // 监听产品变化
            record.on('updated', () => {
                if (record.data.product_id?.[0] !== productId) {
                    // 产品变化了，重新过滤
                    this._filterUnitOptions();
                }
            });
            
        } catch (error) {
            console.error('Error filtering unit options:', error);
        }
    },
});

