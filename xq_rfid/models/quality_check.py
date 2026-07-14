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
        """
        质检通过时自动生成 RFID 标签
        """
        # 如果是 RFID 标签类型的质检，先生成 RFID，再通过质检
        if self.test_type == 'rfid_label' and self.production_id and not self.rfid_tag_id:
            # 使用生产订单的成品批次号
            lot = self.production_id.lot_producing_id

            if lot:
                try:
                    # 调用生产订单的生成方法
                    rfid_tag = self.production_id.generate_rfid_for_lot(
                        lot_id=lot,
                        quality_check_id=self.id
                    )

                    # 关联到当前质检
                    self.rfid_tag_id = rfid_tag.id

                    # 硬件写入被要求时必须成功，否则质检保持未通过。
                    if self.point_id.rfid_device_required:
                        self._write_to_rfid_device(rfid_tag)

                except Exception as e:
                    # RFID 生成失败会抛出异常，阻止质检通过
                    raise UserError(_('RFID 生成失败：%s') % str(e))

        # 如果是 RFID 写入类型的质检，执行 RFID 写入操作
        elif self.test_type == 'rfid_write':
            try:
                self._execute_rfid_write()
            except Exception as e:
                # RFID 写入失败会抛出异常，阻止质检通过
                raise UserError(_('RFID 写入失败：%s') % str(e))

        # 调用父类方法执行质检通过
        res = super(QualityCheck, self).do_pass()

        return res

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
        """Validate the required device and fail until the Adapter exists."""
        del rfid_tag
        device = self.point_id.rfid_device_id
        if not device:
            raise UserError(_('请先配置 RFID 设备！'))
        device._ensure_operational()
        device._raise_adapter_not_configured()

    def _execute_rfid_write(self):
        """
        执行 RFID 写入操作

        此方法处理 rfid_write 类型的质检点
        """
        # 检查是否配置了 RFID 设备
        if not self.point_id.rfid_device_id:
            raise UserError(_('请先配置 RFID 设备！'))

        # 获取 RFID 设备
        device = self.point_id.rfid_device_id

        device._ensure_operational()
        device._raise_adapter_not_configured()

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

