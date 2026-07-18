import re

with open("xq_rfid/wizard/rfid_read_wizard.py", "r") as f:
    content = f.read()

new_action_read = """    def action_read_rfid(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        if not self.device_id:
            raise UserError(_("请选择 RFID 设备。"))
            
        # Ensure operational state (validated, correct type)
        self.device_id._ensure_operational()
            
        epc_hex = self._validate_read_input()
        
        # Build payload
        payload = {
            'target': epc_hex,
            'bank': _BANK_NAMES[self.mem_bank],
            'offset': self.word_ptr,
            'count': self.word_count,
        }
        
        # Use adapter client
        client = self.env['rfid.adapter.client']
        request_id = f"read-wiz-{self.id}-{fields.Datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        try:
            resp = client.submit_operation(self.device_id, request_id, 'read_memory', payload)
            self.read_status = 'reading'
            self.read_result = _("读取任务已提交。请稍后查看结果...")
        except Exception as e:
            self.read_status = 'failed'
            self.read_result = str(e)
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rfid.read.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }"""

content = re.sub(r'    def action_read_rfid\(self\):.*?return self\.device_id\.read_memory\([^)]+\)', new_action_read, content, flags=re.DOTALL)

with open("xq_rfid/wizard/rfid_read_wizard.py", "w") as f:
    f.write(content)
