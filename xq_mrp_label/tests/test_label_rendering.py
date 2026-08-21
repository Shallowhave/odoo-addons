from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("-at_install", "post_install")
class TestLabelBarcodeRendering(TransactionCase):
    def test_scannable_barcode_rendering_contract(self):
        for xmlid in (
            "xq_mrp_label.mrp_label_document",
            "xq_mrp_label.mrp_byproduct_label_document",
        ):
            with self.subTest(template=xmlid):
                root = etree.fromstring(self.env.ref(xmlid).arch_db.encode())
                barcodes = root.xpath(".//*[@t-options-widget=\"'barcode'\"]")

                self.assertEqual(len(barcodes), 1)
                self.assertEqual(barcodes[0].get("t-options-width"), "900")
                self.assertEqual(barcodes[0].get("t-options-height"), "180")
                self.assertEqual(barcodes[0].get("t-options-display_value"), "False")
                self.assertEqual(barcodes[0].getparent().get("class"), "label-barcode")

                styles = "\n".join(root.xpath(".//style/text()"))
                self.assertIn(".label-barcode img", styles)
                self.assertIn("width: 88mm", styles)
                self.assertIn("@page { size: 100mm 100mm; margin: 0; }", styles)
