import sys
import unittest

def load_tests(loader, tests, pattern):
    import xq_rfid.tests.test_adapter_client
    import xq_rfid.tests.test_rfid_device
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(xq_rfid.tests.test_adapter_client))
    suite.addTests(loader.loadTestsFromModule(xq_rfid.tests.test_rfid_device))
    return suite
