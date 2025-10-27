/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState } from "@odoo/owl";

export class ActualQtyInput extends Component {
    static props = {
        name: { type: String, optional: true },
        record: Object,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const d = this.props.record.data;
        this.state = useState({
            actualQty: d.actual_qty || 0,
            isValid: true,
            errorMessage: '',
        });
    }

    parseNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : 0;
    }

    validateActualQty(value) {
        if (value < 0) {
            return {
                isValid: false,
                errorMessage: '实际数量不能为负数'
            };
        }
        if (value === 0) {
            return {
                isValid: false,
                errorMessage: '请输入实际生产数量'
            };
        }
        return {
            isValid: true,
            errorMessage: ''
        };
    }

    onInput(ev) {
        const value = this.parseNumber(ev.target.value);
        this.state.actualQty = value;
        
        // 实时验证
        const validation = this.validateActualQty(value);
        this.state.isValid = validation.isValid;
        this.state.errorMessage = validation.errorMessage;
    }

    async onChange(ev) {
        const value = this.parseNumber(ev.target.value);
        
        // 验证输入
        const validation = this.validateActualQty(value);
        this.state.isValid = validation.isValid;
        this.state.errorMessage = validation.errorMessage;
        
        if (!validation.isValid) {
            this.notification.add(validation.errorMessage, { type: 'danger' });
            return;
        }

        // 只更新本地状态和 record 数据，不立即写入数据库
        // 数据将在质检确认时统一写入
        this.state.actualQty = value;
        this.props.record.data.actual_qty = value;
    }

    async updateField(field, value) {
        // 保持本地状态同步
        this.state.actualQty = value;
        this.props.record.data.actual_qty = value;
        
        try {
            await this.orm.write('quality.check', [this.props.record.data.id], {
                [field]: value,
            });
            
            // 成功更新后显示提示
            this.notification.add(`实际数量已更新为 ${value}`, { type: 'success' });
            
        } catch (e) {
            this.notification.add(`更新失败: ${String(e)}`, { type: 'danger' });
            throw e; // 重新抛出错误以便调用者处理
        }
    }

    // 获取当前输入的样式类
    get inputClass() {
        let baseClass = "form-control";
        if (!this.state.isValid) {
            baseClass += " is-invalid";
        } else if (this.state.actualQty > 0) {
            baseClass += " is-valid";
        }
        return baseClass;
    }

    // 获取显示的占位符文本
    get placeholderText() {
        return "请输入实际生产数量";
    }
}

ActualQtyInput.template = "ps_multi_image_mrp_qc.ActualQtyInput";

registry.category("components").add("ActualQtyInput", ActualQtyInput);
