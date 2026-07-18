import sys
import unittest
import os
sys.path.insert(0, '/usr/lib/python3/dist-packages')
import odoo
odoo.tools.config.parse_config(['-c', '/etc/odoo/odoo.conf'])

# Monkeypatch the odoo.addons namespace
import sys
import odoo.addons
import importlib.util
spec = importlib.util.spec_from_file_location("odoo.addons.xq_rfid", "/opt/custom/addons/.claude/worktrees/si120x1-rfid-integration/xq_rfid/__init__.py")
xq_rfid = importlib.util.module_from_spec(spec)
sys.modules["odoo.addons.xq_rfid"] = xq_rfid
spec.loader.exec_module(xq_rfid)

def load_tests(loader, tests, pattern):
    import odoo.addons.xq_rfid.tests.test_rfid_payload as payload_test
    import odoo.addons.xq_rfid.tests.test_adapter_client as adapter_test
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(payload_test))
    suite.addTests(loader.loadTestsFromModule(adapter_test))
    return suite

if __name__ == '__main__':
    unittest.main()
