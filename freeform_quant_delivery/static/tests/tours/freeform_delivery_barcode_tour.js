/** @odoo-module **/

import { registry } from "@web/core/registry";

const targetLine = (lot, packageName, suffix = "") =>
    `.o_sublines .o_barcode_line${suffix}:has(.o_line_lot_name:contains(${lot})):has(.package:contains(${packageName}))`;

const openPrefilledLine = (lot, packageName) => [
    {
        trigger: ".o_barcode_line_summary",
        run: "click",
    },
    {
        trigger: ".o_line_button.o_toggle_sublines",
        run: "click",
    },
    {
        trigger: targetLine(lot, packageName),
        run: "click",
    },
];

const scanPrefilledPackage = (
    lot,
    decoyLot,
    packageName,
    expectedQuantity
) => [
    {
        trigger: targetLine(lot, packageName, ".o_selected"),
        run: `scan ${packageName}`,
    },
    {
        trigger: `${targetLine(lot, packageName)} .qty-done:contains(${expectedQuantity})`,
        run() {
            if (document.querySelector(".o_notification_bar.bg-danger")) {
                throw new Error("The prefilled source-package scan failed");
            }
        },
    },
    {
        trigger: `${targetLine(decoyLot, packageName)} .qty-done:contains(0)`,
    },
];

const resetSelectedQuantity = (lot, packageName) => [
    {
        trigger: targetLine(lot, packageName),
        run: "click",
    },
    {
        trigger: ".o_barcode_line.o_selected .btn.o_edit",
        run: "click",
    },
    {
        trigger: ".o_form_view_container .o_field_widget[name=qty_done] input",
        run: "clear",
    },
    {
        trigger: ".o_form_view_container .o_field_widget[name=qty_done] input",
        run: "edit 0",
    },
    {
        trigger: ".o_save",
        run: "click",
    },
];

const saveSelectedLine = () => [
    {
        trigger: ".o_barcode_line.o_selected .btn.o_edit",
        run: "click",
    },
    {
        trigger: ".o_form_view_container .o_field_widget[name=qty_done] input",
    },
    {
        trigger: ".o_save",
        run: "click",
    },
    {
        trigger: ".o_barcode_line",
        run: () => {
            if (document.querySelector(".btn.o_validate_page") === null) {
                throw new Error("Barcode validation control should remain available");
            }
        },
    },
];

registry.category("web_tour.tours").add(
    "freeform_delivery_barcode_remaining_quantity",
    {
        steps: () => [
            ...openPrefilledLine("FFD-LOT-IMPLICIT", "FFD-PACKAGE-IMPLICIT"),
            ...scanPrefilledPackage(
                "FFD-LOT-IMPLICIT",
                "FFD-LOT-IMPLICIT-DECOY",
                "FFD-PACKAGE-IMPLICIT",
                "4"
            ),
            ...resetSelectedQuantity(
                "FFD-LOT-IMPLICIT",
                "FFD-PACKAGE-IMPLICIT"
            ),
            {
                trigger: targetLine(
                    "FFD-LOT-IMPLICIT",
                    "FFD-PACKAGE-IMPLICIT"
                ),
                run: "click",
            },
            {
                trigger: targetLine(
                    "FFD-LOT-IMPLICIT",
                    "FFD-PACKAGE-IMPLICIT",
                    ".o_selected"
                ),
                run: "scan FFD-LOT-IMPLICIT",
            },
            {
                trigger:
                    `${targetLine("FFD-LOT-IMPLICIT", "FFD-PACKAGE-IMPLICIT")} .qty-done:contains(4)`,
            },
            ...saveSelectedLine(),
        ],
    }
);

registry.category("web_tour.tours").add(
    "freeform_delivery_barcode_explicit_gs1_quantity",
    {
        steps: () => [
            ...openPrefilledLine("FFD-LOT-EXPLICIT", "FFD-PACKAGE-EXPLICIT"),
            ...scanPrefilledPackage(
                "FFD-LOT-EXPLICIT",
                "FFD-LOT-EXPLICIT-DECOY",
                "FFD-PACKAGE-EXPLICIT",
                "5"
            ),
            ...resetSelectedQuantity(
                "FFD-LOT-EXPLICIT",
                "FFD-PACKAGE-EXPLICIT"
            ),
            {
                trigger: targetLine(
                    "FFD-LOT-EXPLICIT",
                    "FFD-PACKAGE-EXPLICIT"
                ),
                run: "click",
            },
            {
                trigger: targetLine(
                    "FFD-LOT-EXPLICIT",
                    "FFD-PACKAGE-EXPLICIT",
                    ".o_selected"
                ),
                run: "scan 314000000210FFD-LOT-EXPLICIT",
            },
            {
                trigger:
                    `${targetLine("FFD-LOT-EXPLICIT", "FFD-PACKAGE-EXPLICIT")} .qty-done:contains(2)`,
            },
            ...saveSelectedLine(),
        ],
    }
);
