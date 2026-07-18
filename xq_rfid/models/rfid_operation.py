# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import uuid

class RfidOperation(models.Model):
    _name = "rfid.operation"
    _description = "RFID 硬件操作记录"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="参考", required=True, copy=False, readonly=True, default=lambda self: _('New'))
    device_id = fields.Many2one("rfid.device.config", string="RFID 设备", required=True, readonly=True, check_company=True)
    company_id = fields.Many2one("res.company", related="device_id.company_id", store=True, readonly=True)
    
    operation_type = fields.Selection([
        ('write_and_verify', '写后验证'),
        ('inventory', '盘点'),
        ('read_memory', '读取内存'),
    ], string="操作类型", required=True, readonly=True)
    
    status = fields.Selection([
        ('draft', '草稿'),
        ('queued', '排队中'),
        ('processing', '处理中'),
        ('succeeded', '成功'),
        ('failed', '失败'),
        ('cancelled', '已取消'),
    ], string="状态", default='draft', required=True, readonly=True, copy=False)
    
    request_id = fields.Char(string="请求 ID", required=True, readonly=True, copy=False, index=True)
    error_code = fields.Char(string="错误码", readonly=True, copy=False)
    error_message = fields.Text(string="错误信息", readonly=True, copy=False)
    result_data = fields.Text(string="结果数据 (JSON)", readonly=True, copy=False)
    
    quality_check_id = fields.Many2one("quality.check", string="质检项", readonly=True, check_company=True)
    
    _sql_constraints = [
        ('request_id_uniq', 'unique(request_id)', '请求 ID 必须唯一！'),
    ]
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('rfid.operation') or _('New')
            if not vals.get('request_id'):
                vals['request_id'] = str(uuid.uuid4())
        return super().create(vals_list)
        
    @api.model
    def create_or_get_for_quality_check(self, check, device, retry=False):
        """Idempotent operation creation for a quality check"""
        # Find existing operation
        existing_ops = self.search([
            ('quality_check_id', '=', check.id),
            ('operation_type', '=', 'write_and_verify'),
        ], order='create_date desc, id desc')
        
        if existing_ops and not retry:
            return existing_ops[0]
            
        retry_count = len(existing_ops)
        if retry and existing_ops:
            # Cancel the most recent one if it hasn't succeeded
            latest = existing_ops[0]
            if latest.status not in ('succeeded', 'cancelled'):
                latest.status = 'cancelled'
                
        request_id = f"qc-{check.id}-write-{retry_count}"
        
        return self.create({
            'device_id': device.id,
            'operation_type': 'write_and_verify',
            'quality_check_id': check.id,
            'request_id': request_id,
            'status': 'draft',
        })
        
    def action_submit(self):
        """Submits the operation to the adapter client"""
        self.ensure_one()
        if self.status != 'draft':
            raise UserError(_("只能提交草稿状态的操作。"))
            
        client = self.env['rfid.adapter.client']
        try:
            if self.operation_type == 'write_and_verify':
                payload = self.quality_check_id._prepare_rfid_write_data()
                # For label generation, we also need to pass the tag name
                if self.quality_check_id.test_type == 'rfid_label' and self.quality_check_id.rfid_tag_id:
                    payload['rfid_number'] = self.quality_check_id.rfid_tag_id.name
                    
                resp = client.submit_operation(self.device_id, self.request_id, 'write_and_verify', payload)
            elif self.operation_type == 'inventory':
                resp = client.submit_operation(self.device_id, self.request_id, 'inventory', {})
            elif self.operation_type == 'read_memory':
                # Simplified for quality check flows, Task 14 will flesh out the wizard
                resp = client.submit_operation(self.device_id, self.request_id, 'read_memory', {})
                
            self.status = 'queued'
        except Exception as e:
            self.status = 'failed'
            self.error_message = str(e)
            self.error_code = 'submit_error'
        
    def action_sync(self):
        """Synchronizes the operation status from the adapter"""
        self.ensure_one()
        if self.status not in ('queued', 'processing'):
            return
            
        client = self.env['rfid.adapter.client']
        try:
            resp = client.get_operation(self.request_id)
            
            if resp.get('status') == 'completed':
                self.status = 'succeeded'
                self.result_data = str(resp.get('result', {}))
                
                # Write tag counts and TID if applicable
                if self.operation_type == 'write_and_verify' and self.quality_check_id.rfid_tag_id:
                    tag = self.quality_check_id.rfid_tag_id
                    tag.sudo().write_count += 1
                    if 'tid' in resp.get('result', {}):
                        tag.sudo().tid = resp['result']['tid']
                        
            elif resp.get('status') == 'failed':
                self.status = 'failed'
                error = resp.get('error', {})
                self.error_code = error.get('code', 'unknown')
                self.error_message = error.get('message', 'Unknown error from adapter')
            elif resp.get('status') == 'processing':
                self.status = 'processing'
            # 'queued' remains 'queued'
                
        except Exception as e:
            # Don't fail the operation on temporary network errors during sync, just keep it queued
            if "暂时不可用" in str(e) or "timeout" in str(e).lower():
                return
            self.status = 'failed'
            self.error_message = str(e)
            self.error_code = 'sync_error'
            
    @api.model
    def _cron_sync_adapter_results(self, limit=20):
        operations = self.search([
            ('status', 'in', ('queued', 'processing'))
        ], limit=limit, order='create_date asc')
        
        for op in operations:
            op.action_sync()
            self.env.cr.commit()
        
    def action_sync(self):
        """Synchronizes the operation status from the adapter"""
        self.ensure_one()
        if self.status not in ('queued', 'processing'):
            return
        # Task 12 will implement the actual RPC call here
