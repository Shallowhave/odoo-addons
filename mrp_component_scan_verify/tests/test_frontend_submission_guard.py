# -*- coding: utf-8 -*-

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestFrontendSubmissionGuard(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.login = 'component_submission_guard_test'
        new_test_user(
            cls.env,
            login=cls.login,
            groups='base.group_user,base.group_system',
        )

    def test_component_scan_submission_guards(self):
        self.browser_js(
            '/web/tests?headless&loglevel=2&preset=desktop&timeout=15000'
            '&filter=Component scan submission guards',
            '',
            '',
            login=self.login,
            timeout=180,
            success_signal='[HOOT] test suite succeeded',
        )
