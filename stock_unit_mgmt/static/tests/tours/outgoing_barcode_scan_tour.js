/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add(
    "stock_unit_outgoing_lot_full_area_quantity",
    {
        steps: () => [
            {
                trigger: ".o_barcode_client_action",
                run: "scan BARCODE-AREA-LOT",
            },
            {
                trigger: ".o_barcode_line.o_selected .qty-done",
                run() {
                    const quantity = this.anchor.textContent.trim();
                    if (quantity !== "2162.16") {
                        throw new Error(
                            `Expected the scanned roll quantity to be 2162.16 m2, got ${quantity}`
                        );
                    }
                },
            },
        ],
    }
);
