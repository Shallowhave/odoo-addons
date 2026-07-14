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
        """Preflight every check, then preserve singleton parent semantics."""
        for check in self:
            check._prepare_rfid_before_pass()

        result = None
        for check in self:
            result = super(QualityCheck, check).do_pass()
        return result

    def _prepare_rfid_before_pass(self):
        self.ensure_one()
        if self.test_type == 'rfid_label':
            hardware_required = self.point_id.rfid_device_required
            if not self.production_id:
                if hardware_required:
                    raise UserError(_('RFID 标签质检缺少生产订单。'))
                return
            lot = self.production_id.lot_producing_id
            if not lot:
                if hardware_required:
                    raise UserError(_('请先设置成品批次/序列号。'))
                return
            rfid_tag = self.rfid_tag_id or self.production_id.generate_rfid_for_lot(
                lot_id=lot,
                quality_check_id=self.id,
            )
            if not self.rfid_tag_id:
                self.rfid_tag_id = rfid_tag
            if hardware_required:
                self._write_to_rfid_device(rfid_tag)
        elif self.test_type == 'rfid_write':
            self._execute_rfid_write()

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

    def _write_to_rfid_device(self, rfid_tag):
        """Delegate required label writes to the canonical device operation."""
        device = self.point_id.rfid_device_id
        if not device:
            raise UserError(_('请先配置 RFID 设备！'))
        return device.write_and_verify({
            'rfid_number': rfid_tag.name,
            'product_code': self.product_id.default_code or '',
            'product_name': self.product_id.name,
            'lot_number': rfid_tag.stock_prod_lot_id.name,
            'production_date': rfid_tag.production_date,
            'production_order': self.production_id.name,
        })

    def _execute_rfid_write(self):
        """
        执行 RFID 写入操作

        此方法处理 rfid_write 类型的质检点
        """
        # 检查是否配置了 RFID 设备
        if not self.point_id.rfid_device_id:
            raise UserError(_('请先配置 RFID 设备！'))

        device = self.point_id.rfid_device_id
        return device.write_and_verify(self._prepare_rfid_write_data())

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

