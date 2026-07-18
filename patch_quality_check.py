import re

with open("xq_rfid/models/quality_check.py", "r") as f:
    content = f.read()

# Replace do_pass
new_do_pass = """    def do_pass(self):
        \"\"\"Validate the full recordset before any RFID side effect.\"\"\"
        # If this is a callback completion from a successful operation, bypass queuing
        complete_op_id = self.env.context.get('xq_rfid_complete_operation_id')
        if complete_op_id:
            op = self.env['rfid.operation'].browse(complete_op_id)
            if op.status == 'succeeded' and op.quality_check_id.id in self.ids:
                return super(QualityCheck, self).do_pass()

        plans = [check._plan_rfid_before_pass() for check in self]

        any_async = False
        for check, plan in zip(self, plans):
            if check._execute_rfid_pass_plan(plan):
                any_async = True

        # If any check in the recordset queued an async operation, 
        # we DO NOT call super().do_pass() yet. The frontend will poll.
        if any_async:
            return None

        result = None
        for check in self:
            result = super(QualityCheck, check).do_pass()
        return result"""

content = re.sub(r'    def do_pass\(self\):.*?return result', new_do_pass, content, flags=re.DOTALL)

# Replace _execute_rfid_pass_plan
new_execute = """    def _execute_rfid_pass_plan(self, plan):
        self.ensure_one()
        if not plan:
            return False
            
        if plan['operation'] == 'rfid_label':
            finished_lot = plan['finished_lot']
            rfid_tag = plan['rfid_tag'] or self.production_id.generate_rfid_for_lot(
                lot_id=finished_lot,
                quality_check_id=self.id,
            )
            self._ensure_rfid_tag_matches_finished_lot(rfid_tag, finished_lot)
            if not self.rfid_tag_id:
                self.rfid_tag_id = rfid_tag
                
            if plan['hardware_required']:
                return self._queue_rfid_operation(plan)
                
        elif plan['operation'] == 'rfid_write':
            return self._queue_rfid_operation(plan)
            
        return False
        
    def _queue_rfid_operation(self, plan):
        \"\"\"Queues an async RFID operation instead of blocking\"\"\"
        device = self.point_id.rfid_device_id
        if not device:
            raise UserError(_('请先配置 RFID 设备！'))
            
        operation = self.env['rfid.operation'].create_or_get_for_quality_check(
            self, device, retry=self.env.context.get('xq_rfid_retry', False)
        )
        
        if operation.status == 'draft':
            operation.action_submit()
            
        return True
        
    @api.model
    def get_rfid_operation_status(self, check_id):
        \"\"\"Safe RPC for UI polling\"\"\"
        check = self.browse(check_id)
        check.check_access('read')
        
        operation = self.env["rfid.operation"].search(
            [("quality_check_id", "=", check.id)], order="id desc", limit=1
        )
        
        if not operation:
            return {'status': 'none'}
            
        # Trigger a sync if it's pending
        if operation.status in ('queued', 'processing'):
            operation.action_sync()
            
        return {
            'status': operation.status,
            'operation_id': operation.id,
            'error_message': operation.error_message if operation.status == 'failed' else False,
        }"""

content = re.sub(r'    def _execute_rfid_pass_plan\(self, plan\):.*?self\._execute_rfid_write\(plan\[\'payload\'\]\)', new_execute, content, flags=re.DOTALL)

with open("xq_rfid/models/quality_check.py", "w") as f:
    f.write(content)

