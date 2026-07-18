import re

with open("xq_rfid/models/rfid_operation.py", "r") as f:
    content = f.read()

new_methods = """    def action_submit(self):
        \"\"\"Submits the operation to the adapter client\"\"\"
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
        \"\"\"Synchronizes the operation status from the adapter\"\"\"
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
            self.env.cr.commit()"""

content = re.sub(r'    def action_submit\(self\):.*?# Task 12 will implement the actual RPC call here', new_methods, content, flags=re.DOTALL)

with open("xq_rfid/models/rfid_operation.py", "w") as f:
    f.write(content)
