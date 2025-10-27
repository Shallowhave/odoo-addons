/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";

export class RfidGenerationWizard extends Component {
    static props = {
        name: { type: String, optional: true },
        record: Object,
        close: Function,
        validate: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        
        this.state = useState({
            productionLotNumber: '',
        });

        onWillStart(async () => {
            await this.loadProductionLot();
        });
    }

    async loadProductionLot() {
        const recordData = this.props.record.data;
        
        // 获取生产订单的成品批次号
        if (recordData.production_id && recordData.production_id[0]) {
            try {
                const production = await this.orm.read(
                    'mrp.production',
                    [recordData.production_id[0]],
                    ['lot_producing_id']
                );
                
                if (production && production.length > 0 && production[0].lot_producing_id) {
                    this.state.productionLotNumber = production[0].lot_producing_id[1];
                }
            } catch (error) {
                console.error('获取生产批次号失败:', error);
            }
        }
    }
}

RfidGenerationWizard.template = "xq_rfid.RfidGenerationWizard";

registry.category("components").add("RfidGenerationWizard", RfidGenerationWizard);
