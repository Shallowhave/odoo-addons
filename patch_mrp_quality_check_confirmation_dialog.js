import re

with open("xq_rfid/static/src/components/mrp_quality_check_confirmation_dialog.js", "r") as f:
    content = f.read()

new_validate = """    async validate() {
        if (!this.props.record || this.props.record.resModel !== 'quality.check') {
            return super.validate();
        }

        const isRfidLabel = this.props.record.data.test_type === 'rfid_label';
        const isRfidWrite = this.props.record.data.test_type === 'rfid_write';

        if (!isRfidLabel && !isRfidWrite) {
            return super.validate();
        }

        // Only start polling if hardware is required
        let needsPolling = false;
        if (isRfidWrite) {
            needsPolling = true;
        } else if (isRfidLabel) {
            // Need to check if the point requires hardware
            const pointId = this.props.record.data.point_id && this.props.record.data.point_id[0];
            if (pointId) {
                const points = await this.orm.read('quality.point', [pointId], ['rfid_device_required']);
                if (points.length && points[0].rfid_device_required) {
                    needsPolling = true;
                }
            }
        }

        // Trigger original validation which calls do_pass
        const res = await super.validate();

        if (needsPolling) {
            return this._pollRfidStatus();
        }
        return res;
    },

    async _pollRfidStatus() {
        const checkId = this.props.record.resId;
        const maxRetries = 60; // 1 minute max polling (1s interval)
        let retries = 0;

        // Block UI while polling
        this.env.services.ui.block();

        try {
            while (retries < maxRetries) {
                const result = await this.orm.call('quality.check', 'get_rfid_operation_status', [checkId]);

                if (result.status === 'succeeded') {
                    this.env.services.notification.add('RFID操作成功', { type: 'success' });
                    // Complete the check with the context flag
                    await this.orm.call('quality.check', 'do_pass', [[checkId]], {
                        context: { xq_rfid_complete_operation_id: result.operation_id }
                    });
                    if (this.props.close) {
                        this.props.close();
                    }
                    return;
                } else if (result.status === 'failed') {
                    this.env.services.notification.add(`RFID操作失败: ${result.error_message}`, { type: 'danger', sticky: true });
                    return;
                } else if (result.status === 'cancelled') {
                    this.env.services.notification.add('RFID操作已取消', { type: 'warning' });
                    return;
                } else if (result.status === 'none') {
                    // Nothing queued, perhaps validation failed before queued
                    return;
                }

                // queued or processing - wait 1s
                await new Promise(resolve => setTimeout(resolve, 1000));
                retries++;
            }
            this.env.services.notification.add('RFID操作超时', { type: 'danger' });
        } finally {
            this.env.services.ui.unblock();
        }
    },"""

# Insert new methods after setup()
content = re.sub(r'    setup\(\) \{[\s\S]*?\},', lambda m: m.group(0) + '\n\n' + new_validate, content)

with open("xq_rfid/static/src/components/mrp_quality_check_confirmation_dialog.js", "w") as f:
    f.write(content)

