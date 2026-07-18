# -*- coding: utf-8 -*-

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestFrontendSubmissionGuard(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.login = 'submission_guard_test'
        new_test_user(
            cls.env,
            login=cls.login,
            groups='base.group_user,base.group_system',
        )

    def _run_suite(self, suite_filter):
        self.browser_js(
            '/web/tests?headless&loglevel=2&preset=desktop&timeout=15000'
            f'&filter={suite_filter}',
            '',
            '',
            login=self.login,
            timeout=180,
            success_signal='[HOOT] test suite succeeded',
        )

    def test_work_order_submission_guards(self):
        self._run_suite('coalesces concurrent work order')

    def test_close_production_submission_guard(self):
        self._run_suite('coalesces concurrent close production requests')

    def test_quick_production_registration_guard(self):
        self._run_suite('coalesces concurrent quick production registration')

    def test_register_production_dialog_guard(self):
        self._run_suite('register production')
