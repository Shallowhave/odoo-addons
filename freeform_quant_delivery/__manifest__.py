{
    "name": "Free-form Quant Delivery",
    "version": "18.0.1.0.0",
    "author": "memory",
    "category": "Inventory/Inventory",
    "depends": [
        "stock",
        "stock_barcode",
        "barcodes_gs1_nomenclature",
        "stock_unit_mgmt",
        "delivery_report",
        "quality_report",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/freeform_delivery_wizard_views.xml",
        "views/stock_picking_views.xml",
    ],
    "assets": {
        "web.assets_tests": [
            "freeform_quant_delivery/static/tests/tours/"
            "freeform_delivery_barcode_tour.js",
        ],
    },
    "installable": True,
    "license": "LGPL-3",
}
