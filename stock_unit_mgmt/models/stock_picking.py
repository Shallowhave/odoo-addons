# -*- coding: utf-8 -*-

from odoo import models, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        """重写验证方法，在验证前比对扫码数据和预填数据"""
        # **关键修复**：检查作业类型是否启用了增强条码验证
        # 只有当作业类型启用了增强条码验证时，才执行增强验证
        if (self.picking_type_id.code == 'incoming' and 
            self.picking_type_id.enable_enhanced_barcode_validation):
            self._validate_scanned_data()
        
        # 调用父类方法进行正常验证
        result = super(StockPicking, self).button_validate()
        
        # **关键修复**：在入库验证完成后，确保 lot_quantity 和 lot_unit_name 正确保存
        # 入库过程中可能会清空这些字段，需要在验证完成后恢复
        if (self.picking_type_id.code == 'incoming' and 
            self.picking_type_id.enable_enhanced_barcode_validation and
            self.state == 'done'):
            try:
                # 查询所有有批次号的移动行
                move_lines = self.env['stock.move.line'].search([
                    ('picking_id', '=', self.id),
                    ('lot_name', '!=', False),
                    ('lot_name', '!=', ''),
                ])
                
                if move_lines:
                    # 为每个移动行检查和恢复 lot_quantity 和 lot_unit_name
                    for move_line in move_lines:
                        if move_line.lot_name:
                            # 获取产品配置
                            product = move_line.product_id
                            if product and product.product_tmpl_id:
                                product_tmpl = product.product_tmpl_id
                                
                                # 如果 lot_quantity 为空或 0，尝试从产品配置获取
                                if not move_line.lot_quantity or move_line.lot_quantity == 0:
                                    # 检查是否有默认单位配置
                                    if (hasattr(product_tmpl, 'enable_custom_units') and 
                                        product_tmpl.enable_custom_units and
                                        hasattr(product_tmpl, 'default_unit_config') and
                                        product_tmpl.default_unit_config):
                                        # 如果有默认单位配置，但没有 lot_quantity，设置为默认值 1.0
                                        # 注意：这里不设置 lot_quantity，因为用户可能想要手动填写
                                        # 只设置 lot_unit_name
                                        if not move_line.lot_unit_name:
                                            move_line.with_context(skip_quantity_fix=True).write({
                                                'lot_unit_name': product_tmpl.default_unit_config
                                            })
                                            _logger.info(
                                                f"[入库验证后] 恢复 lot_unit_name: 记录ID={move_line.id}, "
                                                f"批次号={move_line.lot_name}, lot_unit_name={product_tmpl.default_unit_config}"
                                            )
                                
                                # 如果 lot_unit_name 为空，尝试从产品配置获取
                                if not move_line.lot_unit_name:
                                    if (hasattr(product_tmpl, 'enable_custom_units') and 
                                        product_tmpl.enable_custom_units and
                                        hasattr(product_tmpl, 'default_unit_config') and
                                        product_tmpl.default_unit_config):
                                        move_line.with_context(skip_quantity_fix=True).write({
                                            'lot_unit_name': product_tmpl.default_unit_config
                                        })
                                        _logger.info(
                                            f"[入库验证后] 恢复 lot_unit_name: 记录ID={move_line.id}, "
                                            f"批次号={move_line.lot_name}, lot_unit_name={product_tmpl.default_unit_config}"
                                        )
            except Exception as e:
                _logger.warning(
                    f"[入库验证后] 恢复单位信息时出错: picking_id={self.id}, 错误={str(e)}",
                    exc_info=True
                )
        
        return result

    def _validate_scanned_data(self):
        """比对扫码数据和预填数据
        
        比对逻辑（按照序列号的方式）：
        1. 预填数据：所有有批次号且已保存的记录（lot_name 不为空）
        2. 扫码数据：所有有批次号且 qty_done > 0 的记录（表示已扫码验证）
        3. 比对批次号列表是否一致：
           - 预填的批次号必须都被扫码（qty_done > 0）
           - 扫码的批次号必须在预填列表中
        4. **关键修改**：按照序列号逻辑，每个批次号对应 quantity = 1.0
           - 不需要比对数量，因为每个批次号就是 1.0
           - 只比对批次号列表是否一致
           - 批次号必须唯一，不能重复
        
        注意：
        - 批次号按照序列号的方式处理：每个批次号对应 1.0 单位
        - 扫码只是验证批次号是否存在，不验证数量
        - 批次号必须唯一，不能重复使用
        - stock.move.line 没有 product_uom_qty 字段，需要从 move_id.product_uom_qty 获取
        """
        import logging
        _logger = logging.getLogger(__name__)
        
        _logger.info(
            f"[验证比对] 开始比对扫码数据和预填数据: 调拨单={self.name}, "
            f"调拨单ID={self.id}"
        )
        
        # **关键修复**：在验证前，同步会话变量与数据库状态
        # 如果数据库中不存在记录，但从会话变量中看到该批次号，说明记录已被删除
        # 需要从会话变量中移除该批次号，避免误判为重复扫描
        try:
            # **关键修复**：在模型中访问 request 需要通过 odoo.http.request
            # 而且需要检查是否有 HTTP 请求上下文（在后台任务或 cron 中可能没有）
            try:
                from odoo.http import request
                if request and hasattr(request, 'session'):
                    session = request.session
                else:
                    # 没有 HTTP 请求上下文，跳过会话操作
                    _logger.debug(f"[验证比对] 没有 HTTP 请求上下文，跳过会话变量同步: picking_id={self.id}")
                    session = None
            except (ImportError, AttributeError, RuntimeError):
                # 无法导入 request 或没有请求上下文，跳过会话操作
                _logger.debug(f"[验证比对] 无法访问 request，跳过会话变量同步: picking_id={self.id}")
                session = None
            
            if session:
                scanned_lots_key = f'scanned_lots_{self.id}'
                scanned_lots = list(session.get(scanned_lots_key, []) or [])
                
                if scanned_lots:
                    # 检查每个批次号是否在数据库中存在
                    # 查询所有有批次号的移动行
                    all_lines_with_lot = self.env['stock.move.line'].search([
                        ('picking_id', '=', self.id),
                        ('lot_name', '!=', False),
                        ('lot_name', '!=', ''),
                    ])
                    
                    # 获取数据库中所有存在的批次号（标准化后），且 qty_done > 0
                    # **关键修复**：只有 qty_done > 0 的批次号才应该保留在会话变量中
                    # qty_done = 0 的批次号是包裹操作，不应该在会话中
                    db_lot_names_with_qty_done = set()
                    for line in all_lines_with_lot:
                        if line.lot_name:
                            try:
                                # 清除缓存，读取最新的 qty_done
                                line.invalidate_recordset(['qty_done'])
                                read_result = line.read(['qty_done'])
                                qty_done = read_result[0].get('qty_done', 0.0) or 0.0 if read_result else 0.0
                                
                                # 只有 qty_done > 0 的批次号才应该保留在会话变量中
                                if qty_done > 0:
                                    db_lot_names_with_qty_done.add(line.lot_name.strip().lower())
                            except Exception as e:
                                # 如果读取失败，使用属性访问
                                try:
                                    qty_done = line.qty_done or 0.0
                                    if qty_done > 0:
                                        db_lot_names_with_qty_done.add(line.lot_name.strip().lower())
                                except:
                                    # 如果还是失败，保守处理：不添加到集合中
                                    pass
                    
                    # 从会话变量中移除数据库中不存在的批次号，或 qty_done = 0 的批次号
                    original_scanned_lots = scanned_lots.copy()
                    scanned_lots = [lot for lot in scanned_lots if lot in db_lot_names_with_qty_done]
                    
                    if len(scanned_lots) != len(original_scanned_lots):
                        session[scanned_lots_key] = scanned_lots
                        session.modified = True
                        removed_lots = set(original_scanned_lots) - set(scanned_lots)
                        _logger.info(
                            f"[验证比对] 同步会话变量: picking_id={self.id}, "
                            f"从会话中移除的批次号={list(removed_lots)} "
                            f"（数据库中不存在或 qty_done=0）, "
                            f"更新后的会话列表={scanned_lots}"
                        )
        except Exception as e:
            _logger.warning(f"[验证比对] 同步会话变量时出错: {str(e)}")
        
        # **关键修改**：按照序列号的方式收集数据
        # 每个批次号对应 1.0 单位，不需要累计数量
        # 预填数据：所有有批次号的记录（lot_name 不为空）
        prefilled_lot_names = set()  # 预填的批次号集合
        prefilled_lot_info = {}  # {批次号: {'product_id': 产品ID, 'product_name': 产品名称, 'move_line_ids': [记录ID列表]}}
        
        # 收集扫码数据：所有有批次号且 qty_done > 0 的记录（表示已扫码验证）
        scanned_lot_names = set()  # 扫码的批次号集合
        scanned_lot_info = {}  # {批次号: {'product_id': 产品ID, 'product_name': 产品名称, 'move_line_ids': [记录ID列表]}}
        
        for move in self.move_ids:
            for line in move.move_line_ids:
                if not line.lot_name:
                    continue
                
                lot_name = line.lot_name.strip()
                if not lot_name:
                    continue
                
                product_id = line.product_id.id
                product_name = line.product_id.name
                
                # **关键修改**：按照序列号的方式，每个批次号对应 1.0 单位
                # 预填数据：所有有批次号的记录
                if lot_name not in prefilled_lot_info:
                    prefilled_lot_info[lot_name] = {
                        'product_id': product_id,
                        'product_name': product_name,
                        'move_line_ids': [],
                    }
                prefilled_lot_info[lot_name]['move_line_ids'].append(line.id)
                prefilled_lot_names.add(lot_name)
                
                # 扫码数据：所有有批次号且 qty_done > 0 的记录（表示已扫码验证）
                # **关键修改**：按照序列号的方式，只需要检查批次号是否存在，不需要检查数量
                if line.qty_done > 0:
                    if lot_name not in scanned_lot_info:
                        scanned_lot_info[lot_name] = {
                            'product_id': product_id,
                            'product_name': product_name,
                            'move_line_ids': [],
                        }
                    scanned_lot_info[lot_name]['move_line_ids'].append(line.id)
                    scanned_lot_names.add(lot_name)
        
        _logger.info(
            f"[验证比对] 预填批次号: {sorted(prefilled_lot_names)}, "
            f"扫码批次号: {sorted(scanned_lot_names)}, "
            f"调拨单={self.name}"
        )
        
        # 如果没有预填数据，跳过比对（允许直接扫码创建，不强制预填）
        if not prefilled_lot_names:
            _logger.info(
                f"[验证比对] 没有预填数据，跳过比对: 调拨单={self.name}"
            )
            return
        
        # **关键修改**：按照序列号的方式，只比对批次号列表，不比对数量
        # 每个批次号对应 1.0 单位，批次号必须唯一
        
        # 检查是否有未扫码的批次号（预填了但没有 qty_done > 0）
        missing_scanned = prefilled_lot_names - scanned_lot_names
        if missing_scanned:
            missing_list = '\n'.join([
                f"  - {lot_name} ({prefilled_lot_info[lot_name]['product_name']})"
                for lot_name in sorted(missing_scanned)
            ])
            error_msg = _(
                '验证失败：以下批次号未扫码！\n\n'
                '未扫码的批次号：\n%s\n\n'
                '请先扫描所有预填的批次号，然后再进行验证。\n\n'
                '批次号按照序列号的方式处理，每个批次号对应 1.0 单位。'
            ) % missing_list
            _logger.error(
                f"[验证比对] 验证失败: 未扫码的批次号={list(missing_scanned)}, "
                f"调拨单={self.name}"
            )
            raise UserError(error_msg)
        
        # 检查是否有扫码了但不在预填列表中的批次号
        extra_scanned = scanned_lot_names - prefilled_lot_names
        if extra_scanned:
            extra_list = '\n'.join([
                f"  - {lot_name} ({scanned_lot_info[lot_name]['product_name']})"
                for lot_name in sorted(extra_scanned)
            ])
            error_msg = _(
                '验证失败：以下批次号不在预填列表中！\n\n'
                '不在预填列表中的批次号：\n%s\n\n'
                '扫码只是验证，批次号必须在预填列表中。\n'
                '批次号按照序列号的方式处理，每个批次号对应 1.0 单位。\n'
                '如需添加新批次号，请手动填写，不要使用扫码。'
            ) % extra_list
            _logger.error(
                f"[验证比对] 验证失败: 不在预填列表中的批次号={list(extra_scanned)}, "
                f"调拨单={self.name}"
            )
            raise UserError(error_msg)
        
        # **关键修改**：按照序列号的方式，不需要比对数量
        # 每个批次号对应 1.0 单位，批次号列表一致即可
        # 比对通过
        _logger.info(
            f"[验证比对] 验证通过: 调拨单={self.name}, "
            f"预填批次号列表={sorted(prefilled_lot_names)}, "
            f"扫码批次号列表={sorted(scanned_lot_names)}, "
            f"批次号列表一致（按照序列号方式，每个批次号对应 1.0 单位）"
        )

