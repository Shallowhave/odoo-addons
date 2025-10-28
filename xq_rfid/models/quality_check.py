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
                    
                    # 如果质检点配置了需要 RFID 设备，则调用写入接口
                    if self.point_id.rfid_device_required:
                        try:
                            self._write_to_rfid_device(rfid_tag)
                        except Exception as e:
                            # 设备写入失败不影响 RFID 生成，只记录日志
                            import logging
                            _logger = logging.getLogger(__name__)
                            _logger.warning('RFID 设备写入失败：%s', str(e))
                    
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
        """
        写入 RFID 设备（硬件接口）
        
        此方法为硬件对接预留接口，子类或其他模块可以重写此方法
        实现具体的硬件写入逻辑
        
        :param rfid_tag: RFID 标签记录
        :return: True 表示写入成功，False 或抛出异常表示失败
        """
        # 调用 RFID 设备服务
        device_service = self.env['rfid.device.service']
        
        # 准备要写入的数据
        write_data = {
            'rfid_number': rfid_tag.name,
            'product_code': self.product_id.default_code or '',
            'product_name': self.product_id.name,
            'lot_number': self.lot_id.name,
            'production_date': rfid_tag.production_date,
            'production_order': self.production_id.name,
        }
        
        # 调用设备写入方法
        result = device_service.write_rfid_tag(write_data)
        
        if not result.get('success'):
            raise UserError(_('RFID 设备写入失败：%s') % result.get('error', '未知错误'))
        
        # 记录写入日志
        rfid_tag.message_post(
            body=_('RFID 标签已写入硬件设备<br/>质检点: %s<br/>设备响应: %s') % (
                self.point_id.title,
                result.get('message', '成功')
            )
        )
        
        return True
    
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
        
        # 检查设备连接状态
        if device.connection_status != 'connected':
            raise UserError(_('RFID 设备未连接，请先测试连接！'))
        
        # 准备要写入的数据
        write_data = self._prepare_rfid_write_data()
        
        # 根据设备类型调用相应的写入服务
        if device.device_type == 'uhf_reader18':
            result = self._write_to_uhf_reader18(device, write_data)
        else:
            # 使用通用设备服务
            device_service = self.env['rfid.device.service']
            result = device_service.write_rfid_tag(write_data)
        
        if not result.get('success'):
            raise UserError(_('RFID 写入失败：%s') % result.get('error', '未知错误'))
        
        # 记录写入日志
        self.message_post(
            body=_('RFID 写入成功<br/>设备: %s<br/>数据: %s<br/>响应: %s') % (
                device.name,
                str(write_data),
                result.get('message', '成功')
            )
        )
        
        return result
    
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
            'quantity': self.qty_done if hasattr(self, 'qty_done') else 1,
            'unit': self.product_id.uom_id.name if self.product_id and self.product_id.uom_id else '',
            'work_center': self.workcenter_id.name if self.workcenter_id else '',
            'workorder': self.workorder_id.name if self.workorder_id else '',
            'operation': self.point_id.title if self.point_id else '',
            'operator': self.user_id.name if self.user_id else '',
        }
        
        return data
    
    def _write_to_uhf_reader18(self, device, write_data):
        """
        使用 UHFReader18 设备写入 RFID
        """
        try:
            # 获取 UHFReader18 服务
            uhf_service = self.env['uhf.reader18.service']
            
            # 准备写入的数据（转换为设备需要的格式）
            formatted_data = self._format_data_for_uhf(write_data)
            
            # 将字符串转换为字节数据
            data_bytes = formatted_data.encode('utf-8')
            
            # 将字节数据转换为字列表（每2个字节为一个字）
            word_list = []
            for i in range(0, len(data_bytes), 2):
                if i + 1 < len(data_bytes):
                    word = (data_bytes[i] << 8) | data_bytes[i + 1]
                else:
                    word = data_bytes[i] << 8  # 最后一个字节
                word_list.append(word)
            
            # 使用默认的EPC（可以从设备配置中获取）
            epc_hex = device.device_address or "000000000000000000000000"
            
            # 执行写入操作
            result = uhf_service.write_data(
                ip=device.ip_address,
                port=int(device.port),
                epc_hex=epc_hex,
                mem_bank=0x01,  # EPC存储区
                word_ptr=0x02,  # 从EPC的第二个字开始写入
                write_data=word_list
            )
            
            return result
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _format_data_for_uhf(self, data):
        """
        将数据格式化为 UHFReader18 设备需要的格式
        """
        # 这里可以根据实际需求格式化数据
        # 例如：将数据转换为十六进制、JSON 或其他格式
        
        # 简单示例：将关键信息组合成字符串
        formatted_data = f"PO:{data.get('production_order', '')}|" \
                        f"PN:{data.get('product_name', '')}|" \
                        f"PC:{data.get('product_code', '')}|" \
                        f"BN:{data.get('batch_number', '')}|" \
                        f"PD:{data.get('production_date', '')}"
        
        return formatted_data

