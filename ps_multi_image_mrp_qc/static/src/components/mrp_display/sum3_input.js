/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState } from "@odoo/owl";

export class Sum3Input extends Component {
    static props = {
        name: { type: String, optional: true },
        record: Object,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const d = this.props.record.data;
        this.state = useState({
            v1: d.sum3_value_1 || 0,
            v2: d.sum3_value_2 || 0,
            v3: d.sum3_value_3 || 0,
        });
    }

    parseNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : 0;
    }

    onInput1(ev) {
        this.state.v1 = this.parseNumber(ev.target.value);
    }
    onInput2(ev) {
        this.state.v2 = this.parseNumber(ev.target.value);
    }
    onInput3(ev) {
        this.state.v3 = this.parseNumber(ev.target.value);
    }

    async onChange1(ev) {
        const value = this.parseNumber(ev.target.value);
        await this.updateField('sum3_value_1', value);
    }
    async onChange2(ev) {
        const value = this.parseNumber(ev.target.value);
        await this.updateField('sum3_value_2', value);
    }
    async onChange3(ev) {
        const value = this.parseNumber(ev.target.value);
        await this.updateField('sum3_value_3', value);
    }

    async updateField(field, value) {
        // keep local state in sync as well
        if (field === 'sum3_value_1') this.state.v1 = value;
        if (field === 'sum3_value_2') this.state.v2 = value;
        if (field === 'sum3_value_3') this.state.v3 = value;
        try {
            await this.orm.write('quality.check', [this.props.record.data.id], {
                [field]: value,
            });
        } catch (e) {
            this.notification.add(String(e), { type: 'danger' });
        }
    }
}

Sum3Input.template = "ps_multi_image_mrp_qc.Sum3Input";

registry.category("components").add("Sum3Input", Sum3Input);


