# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import MagicMock

from odoo.addons.mrp_production_return.models.mrp_production_return_wizard import (
    MrpProductionReturnWizard,
)
from odoo.tests import TransactionCase


class _FakeHistory:
    def __init__(self):
        self.picking_id = SimpleNamespace(state='done')
        self.state = 'draft'

    def sudo(self):
        return self

    def with_context(self, **_context):
        return self

    def action_done(self):
        self.state = 'done'


class _FakeHistoryModel:
    def __init__(self):
        self.histories = []

    def sudo(self):
        return self

    def with_context(self, **_context):
        return self

    def create(self, _values):
        history = _FakeHistory()
        self.histories.append(history)
        return history


class _FakeEnv:
    def __init__(self, history_model):
        self.user = SimpleNamespace(id=1)
        self.history_model = history_model

    def __getitem__(self, model_name):
        if model_name != 'mrp.production.return.history':
            raise AssertionError(model_name)
        return self.history_model


class TestMrpProductionReturnWizard(TransactionCase):
    def test_source_moves_are_locked_in_stable_order(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [(2,), (7,), (9,)]
        wizard = SimpleNamespace(env=SimpleNamespace(cr=cursor))
        lines = MagicMock()
        lines.mapped.return_value.ids = [9, 2, 7]

        MrpProductionReturnWizard._lock_source_moves(wizard, lines)

        self.assertEqual(
            cursor.execute.call_args_list,
            [
                (
                    (
                        'SELECT id FROM stock_move '
                        'WHERE id IN %s ORDER BY id FOR UPDATE',
                        [(2, 7, 9)],
                    ),
                    {},
                ),
                (
                    (
                        'UPDATE stock_move SET write_date = write_date WHERE id IN %s',
                        [(2, 7, 9)],
                    ),
                    {},
                ),
            ],
        )
        cursor.fetchall.assert_called_once_with()

    def test_validation_action_does_not_skip_remaining_components(self):
        events = []
        action = {'type': 'ir.actions.act_window', 'res_model': 'stock.backorder.confirmation'}
        history_model = _FakeHistoryModel()
        company = SimpleNamespace(id=1)
        lines = [
            SimpleNamespace(
                id=1,
                return_qty=1.0,
                product_id=SimpleNamespace(id=11),
                move_id=SimpleNamespace(id=21),
            ),
            SimpleNamespace(
                id=2,
                return_qty=1.0,
                product_id=SimpleNamespace(id=12),
                move_id=SimpleNamespace(id=22),
            ),
        ]
        wizard = SimpleNamespace(
            env=_FakeEnv(history_model),
            production_id=SimpleNamespace(id=31, company_id=company, name='MO/TEST'),
            component_line_ids=lines,
            return_strategy='before',
            target_location_id=SimpleNamespace(id=41),
            return_reason_id=False,
            custom_reason=False,
            notes=False,
            send_notification=False,
            complete_production_after_return=False,
            auto_confirm_picking=True,
            ensure_one=lambda: None,
        )
        wizard._validate_data = lambda: events.append('validate') or lines
        wizard._lock_source_moves = lambda valid_lines: events.append(
            ('lock', [line.move_id.id for line in valid_lines])
        )
        wizard.with_company = lambda _company: wizard
        wizard._process_location_return = lambda _history, line: (
            events.append(('process', line.id)) or (action if line.id == 1 else False)
        )

        result = MrpProductionReturnWizard.action_confirm_return(wizard)

        self.assertEqual(result, action)
        self.assertEqual(
            events,
            [
                'validate',
                ('lock', [21, 22]),
                'validate',
                ('process', 1),
                ('process', 2),
            ],
        )
        self.assertEqual(len(history_model.histories), 2)
        self.assertTrue(all(history.state == 'done' for history in history_model.histories))
