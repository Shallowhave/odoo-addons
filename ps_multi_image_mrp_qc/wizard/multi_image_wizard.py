# -*- coding: utf-8 -*-

from odoo import models, fields, _
from odoo.exceptions import UserError
import base64


class InheritMrpProductionWorkcenterLine(models.Model):
    _inherit = 'mrp.workorder'

    multi_picture = fields.Many2many('ir.attachment', related='current_quality_check_id.multi_picture', string='Image/PDF', readonly=False)


class InheritQualityCheck(models.Model):
    _inherit = "quality.check"

    multi_picture = fields.Many2many('ir.attachment',string='Image/PDF', store=True)

    def write(self, vals):
        if 'multi_picture' in vals:
            existing_attachments = self.multi_picture.ids
            new_attachments = set(existing_attachments)
            for command in vals['multi_picture']:
                if command[0] == 4:
                    new_attachments.add(command[1])
                elif command[0] == 3:
                    new_attachments.discard(command[1])
                elif command[0] == 6:
                    new_attachments = set(command[2])

            removed_attachments = set(existing_attachments) - new_attachments
            if removed_attachments:
                self.env['ir.attachment'].browse(list(removed_attachments)).unlink()

        return super(InheritQualityCheck, self).write(vals)

    def save_multi_attachments(self, record_id,attachments):
        record = self.browse(record_id)
        attachments = self.browse(attachments)
        if record.exists():
            for attachment in attachments:
                self.env['ir.attachment'].create({
                    'name': attachment['name'],
                    'res_model': 'quality.check',
                    'res_id': record.id,
                    'type': 'binary',
                    'datas': base64.b64encode(attachment['content'].encode()),
                })
        return True

class InheritQualityCheckWizard(models.TransientModel):
    _inherit = 'quality.check.wizard'

    multi_picture = fields.Many2many('ir.attachment', related='current_check_id.multi_picture', string='Image/PDF', readonly=False)


    def do_pass(self):
        if self.test_type == 'picture' and not self.picture:
            raise UserError(_('You must provide a picture before validating'))
        if self.test_type == 'multipic' and not self.multi_picture:
            raise UserError(_('You must provide a pictures before validating'))
        self.current_check_id.do_pass()
        return self.action_generate_next_window()

