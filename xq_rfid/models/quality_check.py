# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import UserError


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    # RFID 标签关联
    rfid_tag_id = fields.Many2one(
        'rfid.tag',
        string='RFID 标签',
        readonly=True,
        help='质检时生成的 RFID 标签'
    )

    rfid_tag_name = fields.Char(
        related='rfid_tag_id.name',
        string='RFID 编号',
        readonly=True
    )

    test_type = fields.Char(
        string='测试类型',
        related='point_id.test_type_id.technical_name',
        readonly=True,
        help='质检点的技术名称，用于判断是否为 RFID 测试'
    )

    # RFID 写入内容显示
    rfid_write_content = fields.Text(
        string='RFID 写入内容',
        compute='_compute_rfid_write_content',
        readonly=True,
        help='要写入到 RFID 标签的数据内容'
    )

    @api.depends('test_type', 'production_id', 'product_id', 'lot_id')
    def _compute_rfid_write_content(self):
        """计算RFID写入内容"""
        for record in self:
            if record.test_type == 'rfid_write':
                # 准备要写入的数据
                write_data = record._prepare_rfid_write_data()

                # 格式化为可读的文本
                content_lines = []
                content_lines.append("=== RFID 写入数据 ===")
                content_lines.append(f"生产订单: {write_data.get('production_order', '')}")
                content_lines.append(f"产品名称: {write_data.get('product_name', '')}")
                content_lines.append(f"产品编码: {write_data.get('product_code', '')}")
                content_lines.append(f"批次号: {write_data.get('batch_number', '')}")
                content_lines.append(f"生产日期: {write_data.get('production_date', '')}")
                content_lines.append(f"数量: {write_data.get('quantity', '')}")
                content_lines.append(f"单位: {write_data.get('unit', '')}")
                content_lines.append(f"工作中心: {write_data.get('work_center', '')}")
                content_lines.append(f"工单: {write_data.get('workorder', '')}")
                content_lines.append(f"工序: {write_data.get('operation', '')}")
                content_lines.append(f"操作员: {write_data.get('operator', '')}")
                content_lines.append("==================")

                record.rfid_write_content = '\n'.join(content_lines)
            else:
                record.rfid_write_content = ''

    def do_pass(self):
        """Validate the full recordset before any RFID side effect."""
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
        return result

    def _plan_rfid_before_pass(self):
        """Return a read-only execution plan for one quality check."""
        self.ensure_one()
        if self.test_type == 'rfid_label':
            hardware_required = self.point_id.rfid_device_required
            if not self.production_id:
                if hardware_required:
                    raise UserError(_('RFID 标签质检缺少生产订单。'))
                return None
            finished_lot = self.production_id.lot_producing_id
            if not finished_lot:
                if hardware_required:
                    raise UserError(_('请先设置成品批次/序列号。'))
                return None
            self._ensure_finished_lot_matches_production(finished_lot)
            rfid_tag = self.rfid_tag_id or self.env['rfid.tag'].search([
                ('stock_prod_lot_id', '=', finished_lot.id),
            ], limit=1)
            if rfid_tag:
                self._ensure_rfid_tag_matches_finished_lot(rfid_tag, finished_lot)
            device = self.point_id.rfid_device_id
            if hardware_required:
                self._ensure_rfid_device_ready(device)
            return {
                'operation': 'rfid_label',
                'finished_lot': finished_lot,
                'rfid_tag': rfid_tag,
                'hardware_required': hardware_required,
            }
        if self.test_type == 'rfid_write':
            device = self.point_id.rfid_device_id
            self._ensure_rfid_device_ready(device)
            return {
                'operation': 'rfid_write',
                'payload': self._prepare_rfid_write_data(),
            }
        return None

    def _execute_rfid_pass_plan(self, plan):
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
        """Queues an async RFID operation instead of blocking"""
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
        """Safe RPC for UI polling"""
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
        }

    def _ensure_finished_lot_matches_production(self, finished_lot):
        self.ensure_one()
        if self.production_id.company_id not in self.env.companies:
            raise UserError(_('无权访问该生产订单的公司。'))
        if finished_lot.product_id != self.production_id.product_id:
            raise UserError(_('成品批次产品与生产订单产品不一致。'))
        if finished_lot.company_id != self.production_id.company_id:
            raise UserError(_('成品批次与生产订单公司不一致。'))
        if self.product_id and self.product_id != self.production_id.product_id:
            raise UserError(_('质检产品与生产订单产品不一致。'))

    def _ensure_rfid_device_ready(self, device):
        if not device:
            raise UserError(_('请先配置 RFID 设备！'))
        device._ensure_rfid_manager()
        device._ensure_operational()

    def _ensure_rfid_tag_matches_finished_lot(self, rfid_tag, finished_lot):
        self.ensure_one()
        if rfid_tag.stock_prod_lot_id != finished_lot:
            raise UserError(_('现有 RFID 标签与生产订单的成品批次不一致。'))
        if finished_lot.product_id != self.production_id.product_id:
            raise UserError(_('成品批次产品与生产订单产品不一致。'))
        if rfid_tag.product_id and rfid_tag.product_id != self.production_id.product_id:
            raise UserError(_('现有 RFID 标签关联了不同的产品。'))
        if rfid_tag.production_id and rfid_tag.production_id != self.production_id:
            raise UserError(_('现有 RFID 标签关联了不同的生产订单。'))
        if rfid_tag.company_id != self.production_id.company_id:
            raise UserError(_('现有 RFID 标签与生产订单公司不一致。'))

    def action_view_rfid_tag(self):
        """查看关联的 RFID 标签"""
        self.ensure_one()

        if not self.rfid_tag_id:
            raise UserError(_('该质检点未生成 RFID 标签！'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('RFID 标签'),
            'res_model': 'rfid.tag',
            'res_id': self.rfid_tag_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _write_to_rfid_device(self, rfid_tag, finished_lot):
        """Delegate a validated finished-lot label to the canonical device."""
        device = self.point_id.rfid_device_id
        if not device:
            raise UserError(_('请先配置 RFID 设备！'))
        product = self.production_id.product_id
        return device.write_and_verify({
            'rfid_number': rfid_tag.name,
            'product_code': product.default_code or '',
            'product_name': product.name,
            'lot_number': finished_lot.name,
            'production_date': rfid_tag.production_date,
            'production_order': self.production_id.name,
        })

    def _execute_rfid_write(self, payload=None):
        """Execute the already validated RFID write plan."""
        device = self.point_id.rfid_device_id
        if not device:
            raise UserError(_('请先配置 RFID 设备！'))
        return device.write_and_verify(payload or self._prepare_rfid_write_data())

    def _prepare_rfid_write_data(self):
        """
        准备 RFID 写入数据
        """
        data = {
            'production_order': self.production_id.name if self.production_id else '',
            'product_name': self.product_id.name if self.product_id else '',
            'product_code': self.product_id.default_code if self.product_id else '',
            'batch_number': self.lot_id.name if self.lot_id else '',
            'production_date': fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'quantity': self.quantity if 'quantity' in self._fields else (self.qty_done if 'qty_done' in self._fields else 1),
            'unit': self.product_id.uom_id.name if self.product_id and self.product_id.uom_id else '',
            'work_center': self.workcenter_id.name if self.workcenter_id else '',
            'workorder': self.workorder_id.name if self.workorder_id else '',
            'operation': self.point_id.title if self.point_id else '',
            'operator': self.user_id.name if self.user_id else '',
        }

        return data

