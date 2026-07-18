import sys
from odoo.tests.common import TransactionCase, tagged
from odoo.modules.module import get_module_resource

print("Testing loading module...")
try:
    import xq_rfid.tests.test_adapter_client as tc
    print("Test classes found:", dir(tc))
except Exception as e:
    print("Failed to import:", e)
