# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import base64
import json


class MultiImageQCHttpController(http.Controller):

    @http.route('/custom/attachment/remove', type='json', auth='user')
    def remove_attachment(self, attachment_id, res_id):
        res_model = 'quality.check'
        attachment = request.env['ir.attachment'].sudo().browse(attachment_id)
        if attachment:
            attachment.unlink()
            # Remove the reference to the attachment in the related model
            record = request.env[res_model].sudo().browse(res_id)
            if record and 'multi_picture' in record._fields:
                record.multi_picture = [(3, attachment_id, 0)]

        return {'success': True}

    @http.route('/custom/attachment/get', type='json', auth='user')
    def get_attachments(self,res_id):
        res_model = 'quality.check'
        attachments = request.env['ir.attachment'].search([
            ('res_id', '=', res_id),
            ('res_model', '=', res_model)
        ])
        return [{
            'id': attachment.id,
            'name': attachment.name,
            'url': '/web/content/' + str(attachment.id),
            'access_token': attachment.access_token
        } for attachment in attachments]


    @http.route('/custom/attachment/add', type='json', auth="user", methods=['POST'])
    def custom_attachment_add(self, res_id, attachments):
        res_model = 'quality.check'
        try:
            record = request.env[res_model].browse(res_id)
            attachment_ids = [attachment['id'] for attachment in attachments]

            # Update the multi_picture field with the new attachments
            if record.exists():
                record.write({
                    'multi_picture': [(4, attachment_id) for attachment_id in attachment_ids],
                })
                record.write({
                    'quality_state': 'pass',
                })

            return {'success': True}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @http.route('/portal/attachment/add', type='http', auth='user', methods=['POST'], csrf=True)
    def custom_method(self, name, file, res_model, res_id, access_token=None, **kwargs):
        IrAttachment = request.env['ir.attachment']

        attachment = IrAttachment.create({
            'name': name,
            'datas': base64.b64encode(file.read()),
            'res_model': 'quality.check',
            'res_id': res_id,
            'access_token': IrAttachment._generate_access_token(),
        })
        return request.make_response(
            data=json.dumps(attachment.read(['id', 'name', 'mimetype', 'file_size', 'access_token'])[0]),
            headers=[('Content-Type', 'application/json')]
        )


    @http.route('/multi_image_qc/custom', type='http', auth='user', methods=['POST'], csrf=True)
    def save_attachments(self, attachments, recordId, csrf_token=None, **kwargs):
        # Parse the attachments
        attachments = json.loads(attachments)
        record = request.env['quality.check'].browse(int(recordId))

        if record.exists():
            for attachment in attachments:
                # Save each attachment to the record
                request.env['ir.attachment'].create({
                    'name': attachment['name'],
                    'res_model': 'quality.check',
                    'res_id': record.id,
                    'type': 'binary',
                    'datas': base64.b64encode(attachment['content']),
                })
        return request.redirect('/web')
