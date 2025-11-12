# -*- coding: utf-8 -*-

from odoo import models, fields, api
import logging

from . import utils

_logger = logging.getLogger(__name__)


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    # 单位名称字段
    lot_unit_name = fields.Selection([
        ('kg', '公斤(kg)'),
        ('roll', '卷'),
        ('barrel', '桶'),
        ('box', '箱'),
        ('bag', '袋'),
        ('sqm', '平方米(㎡)'),
        ('piece', '件'),
        ('custom', '自定义')
    ], string='单位名称', help='计量单位名称（如：桶、卷、件、箱等）', compute='_compute_lot_unit_info', store=True)
    
    # 自定义单位名称字段
    lot_unit_name_custom = fields.Char(
        string='自定义单位名称', 
        help='当选择"自定义"时填写具体的单位名称',
        compute='_compute_lot_unit_info', store=True
    )
    
    # 单位数量字段
    lot_quantity = fields.Float(
        string='单位数量', 
        help='实际收到的单位数量',
        compute='_compute_lot_unit_info', 
        store=True,
        digits=(16, 2)  # 最多16位，小数点后2位
    )
    
    # 格式化的单位显示
    lot_unit_display = fields.Char(
        string='附加单位',
        compute='_compute_lot_unit_display',
        help='格式化显示的附加单位信息，如："5 卷"'
    )
    
    # 备注字段
    o_note1 = fields.Char(string='备注1')
    o_note2 = fields.Char(string='备注2')
    contract_no = fields.Char(string='合同号')

    # 计算字段：长度（根据面积和宽度计算）
    calculated_length_m = fields.Float(
        string='计算长度 (m)',
        compute='_compute_calculated_length',
        store=True,  # 存储计算字段以支持求和
        digits=(16, 2),
        help='根据库存数量（面积）和产品宽度自动计算的长度'
    )

    @api.depends('lot_id', 'product_id', 'quantity', 'location_id')
    def _compute_lot_unit_info(self):
        """从批次记录中获取单位信息，累加所有入库，减去所有出库
        
        注意：由于计算字段的依赖关系限制，当移动行发生变化时，
        需要确保相关的 stock_quant 记录被标记为需要重新计算。
        """
        # 优化：批量加载所有相关的移动行，避免N+1查询
        if not self:
            return
        
        # 收集所有需要查询的 lot_id 和 product_id
        lot_ids = self.filtered('lot_id').mapped('lot_id').ids
        product_ids = self.mapped('product_id').ids
        
        if not lot_ids or not product_ids:
            # 如果没有批次或产品，直接返回
            for quant in self:
                quant.lot_unit_name = False
                quant.lot_unit_name_custom = False
                quant.lot_quantity = 0.0
            return
        
        # 一次性查询所有相关的移动行
        all_move_lines = self.env['stock.move.line'].search([
            ('lot_id', 'in', lot_ids),
            ('product_id', 'in', product_ids),
            ('state', '=', 'done')
        ])
        
        # 建立索引：按 (lot_id, product_id) 索引
        # 用于快速查找相关的移动行
        # 注意：存储为 recordset，而不是 list
        move_lines_by_key = {}
        for ml in all_move_lines:
            key = (ml.lot_id.id, ml.product_id.id)
            if key not in move_lines_by_key:
                move_lines_by_key[key] = self.env['stock.move.line']  # 初始化为空 recordset
            move_lines_by_key[key] |= ml  # 使用 |= 操作符添加到 recordset
        
        # 处理每个库存记录
        for quant in self:
            if not quant.product_id:
                quant.lot_unit_name = False
                quant.lot_unit_name_custom = False
                quant.lot_quantity = 0.0
                continue
            
            # 如果没有批次号，尝试从产品配置获取单位信息
            if not quant.lot_id:
                product_tmpl = quant.product_id.product_tmpl_id
                if hasattr(product_tmpl, 'get_unit_config_for_stock_move'):
                    try:
                        unit_configs = product_tmpl.get_unit_config_for_stock_move()
                        if unit_configs:
                            config = unit_configs[0]
                            quant.lot_unit_name = config['name']
                            # 没有批次号时，单位数量设为库存数量（如果有配置）
                            quant.lot_quantity = quant.quantity if quant.quantity > 0 else 0.0
                        else:
                            quant.lot_unit_name = False
                            quant.lot_unit_name_custom = False
                            quant.lot_quantity = 0.0
                    except Exception:
                        quant.lot_unit_name = False
                        quant.lot_unit_name_custom = False
                        quant.lot_quantity = 0.0
                else:
                    quant.lot_unit_name = False
                    quant.lot_unit_name_custom = False
                    quant.lot_quantity = 0.0
                continue
            
            try:
                # 从批量加载的移动行中获取当前记录相关的移动行
                key = (quant.lot_id.id, quant.product_id.id)
                relevant_move_lines = move_lines_by_key.get(key, self.env['stock.move.line'])
                
                # 筛选入库和出库移动行
                # 注意：入库是指 destination 是当前 quant 的位置，出库是指 source 是当前 quant 的位置
                # 直接使用位置ID匹配（因为 stock.quant 的位置应该是精确的）
                incoming_move_lines = relevant_move_lines.filtered(
                    lambda ml: ml.location_dest_id.id == quant.location_id.id
                )
                
                outgoing_move_lines = relevant_move_lines.filtered(
                    lambda ml: ml.location_id.id == quant.location_id.id
                )
                
                # 累加入库的单位数量（只累加有 lot_quantity 的移动行）
                # 重要：确保读取所有移动行的 lot_quantity 值
                incoming_with_lot_qty = incoming_move_lines.filtered(
                    lambda ml: ml.lot_quantity and ml.lot_quantity > 0
                )
                total_incoming = 0.0
                for ml in incoming_with_lot_qty:
                    total_incoming += ml.lot_quantity
                
                # 累加出库的单位数量（只累加有 lot_quantity 的移动行）
                outgoing_with_lot_qty = outgoing_move_lines.filtered(
                    lambda ml: ml.lot_quantity and ml.lot_quantity > 0
                )
                total_outgoing = 0.0
                for ml in outgoing_with_lot_qty:
                    total_outgoing += ml.lot_quantity
                
                # 计算当前剩余的单位数量
                current_lot_quantity = total_incoming - total_outgoing
                
                # 调试日志：记录详细信息，特别是当没有单位信息时
                product_code = quant.product_id.default_code or quant.product_id.name
                lot_name = quant.lot_id.name if quant.lot_id else 'None'
                # 检查是否缺少单位信息（在计算完成后检查）
                # 如果产品编号包含 250PY2M5001241145a207602，总是记录日志
                should_log = (product_code and '250PY2M5001241145a207602' in str(product_code)) or \
                             (lot_name and '250PY2M5001241145a207602' in str(lot_name)) or \
                             ((not current_lot_quantity or current_lot_quantity <= 0) and quant.quantity > 0)
                
                if should_log:
                    # 记录详细信息用于调试
                    _logger.info(f"[批次数量计算] 产品={product_code}, "
                                f"批次={lot_name}, "
                                f"位置={quant.location_id.name if quant.location_id else 'None'}, "
                                f"位置ID={quant.location_id.id if quant.location_id else 'None'}, "
                                f"库存数量={quant.quantity}, "
                                f"所有移动行数={len(relevant_move_lines)}, "
                                f"入库移动行数={len(incoming_move_lines)}, "
                                f"有数量入库行数={len(incoming_with_lot_qty)}, "
                                f"总入库数量={total_incoming}, "
                                f"总出库数量={total_outgoing}, "
                                f"计算出的单位数量={current_lot_quantity}")
                    # 记录移动行详情
                    if relevant_move_lines:
                        for ml in relevant_move_lines[:5]:  # 记录前5条
                            _logger.info(f"  -> 移动行 {ml.id}: lot_quantity={ml.lot_quantity}, "
                                        f"lot_unit_name={ml.lot_unit_name}, "
                                        f"location_dest={ml.location_dest_id.name if ml.location_dest_id else 'None'}(ID:{ml.location_dest_id.id if ml.location_dest_id else 'None'}), "
                                        f"location_id={ml.location_id.name if ml.location_id else 'None'}(ID:{ml.location_id.id if ml.location_id else 'None'}), "
                                        f"state={ml.state}")
                    else:
                        _logger.info(f"  -> 没有找到相关的移动行")
                
                # 详细调试日志（仅在启用详细日志时输出）
                if self.env['ir.config_parameter'].sudo().get_param('stock_unit_mgmt.enable_debug_logging', 'False').lower() == 'true':
                    incoming_details = [(ml.id, ml.lot_quantity) for ml in incoming_with_lot_qty]
                    _logger.debug(f"[批次数量计算] 批次={quant.lot_id.name if quant.lot_id else 'None'}, "
                                 f"位置={quant.location_id.name if quant.location_id else 'None'}, "
                                 f"所有移动行数={len(relevant_move_lines)}, "
                                 f"入库移动行数={len(incoming_move_lines)}, "
                                 f"有数量入库行数={len(incoming_with_lot_qty)}, "
                                 f"总入库数量={total_incoming}, "
                                 f"总出库数量={total_outgoing}")
                
                # 如果还有库存但单位数量为0或负数，说明可能出库时没有填写单位数量
                # 在这种情况下，按比例计算
                if quant.quantity > 0 and current_lot_quantity <= 0 and total_incoming > 0:
                    # 找到总的入库数量
                    total_incoming_qty = sum(incoming_move_lines.mapped('qty_done') or [0.0]) or \
                                        sum(incoming_move_lines.mapped('quantity') or [0.0])
                    if total_incoming_qty > 0:
                        # 按比例计算：当前库存数量 / 总入库数量 * 总入库单位数量
                        current_lot_quantity = (quant.quantity / total_incoming_qty) * total_incoming
                
                # 取最新的移动行来获取单位名称（优先入库，其次出库）
                latest_move_line = relevant_move_lines.filtered(
                    lambda ml: ml.lot_unit_name
                ).sorted(key='id', reverse=True)[:1]
                
                # 获取单位名称
                if latest_move_line:
                    quant.lot_unit_name = latest_move_line.lot_unit_name
                    quant.lot_unit_name_custom = latest_move_line.lot_unit_name_custom
                elif incoming_with_lot_qty:
                    # 如果没有移动行但有入库数据，尝试从入库移动行获取
                    latest_incoming = incoming_with_lot_qty.sorted(key='id', reverse=True)[:1]
                    if latest_incoming:
                        quant.lot_unit_name = latest_incoming.lot_unit_name
                        quant.lot_unit_name_custom = latest_incoming.lot_unit_name_custom
                    else:
                        quant.lot_unit_name = False
                        quant.lot_unit_name_custom = False
                else:
                    # 如果找不到移动行，尝试从产品配置获取
                    product_tmpl = quant.product_id.product_tmpl_id
                    if hasattr(product_tmpl, 'get_unit_config_for_stock_move'):
                        try:
                            unit_configs = product_tmpl.get_unit_config_for_stock_move()
                            if unit_configs:
                                config = unit_configs[0]
                                quant.lot_unit_name = config['name']
                            else:
                                quant.lot_unit_name = False
                                quant.lot_unit_name_custom = False
                        except Exception:
                            quant.lot_unit_name = False
                            quant.lot_unit_name_custom = False
                    else:
                        quant.lot_unit_name = False
                        quant.lot_unit_name_custom = False
                
                # 设置单位数量（确保不为负数）
                quant.lot_quantity = max(0.0, current_lot_quantity)
            except Exception as e:
                # 错误处理：确保即使出错也不会导致系统崩溃
                import logging
                _log = logging.getLogger(__name__)
                _log.error(
                    f"[批次数量计算错误] 批次={quant.lot_id.name if quant.lot_id else 'None'}, "
                    f"位置={quant.location_id.name if quant.location_id else 'None'}, "
                    f"错误={str(e)}",
                    exc_info=True
                )
                quant.lot_unit_name = False
                quant.lot_unit_name_custom = False
                quant.lot_quantity = 0.0
    
    @api.depends('lot_quantity', 'lot_unit_name', 'lot_unit_name_custom')
    def _compute_lot_unit_display(self):
        """计算格式化的单位显示"""
        for quant in self:
            if quant.lot_quantity and quant.lot_unit_name:
                if quant.lot_unit_name == 'custom':
                    unit_name = quant.lot_unit_name_custom or '单位'
                else:
                    unit_name = utils.get_unit_display_name_cn(quant.lot_unit_name)
                quant.lot_unit_display = f"{quant.lot_quantity} {unit_name}"
            else:
                quant.lot_unit_display = ""
    
    @api.depends('quantity', 'product_id', 'product_id.product_tmpl_id.product_width', 'product_id.product_tmpl_id.uom_id')
    def _compute_calculated_length(self):
        """根据面积和宽度计算长度
        
        计算公式：长度(m) = 面积(㎡) / (宽度(mm) / 1000)
        适用条件：
        1. 产品主单位是"平米"或"平方米"
        2. 产品配置了宽度
        3. 库存数量（面积）大于0
        """
        for quant in self:
            if not quant.product_id or not quant.quantity or quant.quantity <= 0:
                quant.calculated_length_m = 0.0
                continue
            
            product = quant.product_id
            product_tmpl = product.product_tmpl_id
            
            # 检查主单位是否是平米
            # 从 product.product 获取主单位（字段名是 product_uom，不是 product_uom_id）
            uom_id = product.product_uom if hasattr(product, 'product_uom') else product_tmpl.uom_id
            if not uom_id:
                quant.calculated_length_m = 0.0
                continue
            
            # 获取单位名称（支持多语言和JSON格式）
            uom_name = ''
            try:
                # 尝试获取当前语言环境下的名称
                lang = self.env.context.get('lang', 'zh_CN')
                uom_name = uom_id.with_context(lang=lang).name or ''
                # 如果获取不到，尝试直接获取
                if not uom_name:
                    uom_name = uom_id.name or ''
                # 如果是字典格式（JSON），尝试提取中文
                if isinstance(uom_name, dict):
                    uom_name = uom_name.get('zh_CN') or uom_name.get('en_US') or str(uom_name)
                uom_name = str(uom_name)
            except Exception as e:
                _logger.warning(f"[计算长度] 获取单位名称失败: {str(e)}")
                uom_name = str(uom_id.name or '')
            
            # 支持多种平米单位名称
            uom_name_lower = uom_name.lower()
            is_sqm_unit = (
                '平米' in uom_name or 
                '平方米' in uom_name or 
                'sqm' in uom_name_lower or
                'm²' in uom_name or
                'm2' in uom_name_lower or
                # 检查单位类别名称
                (hasattr(uom_id, 'category_id') and uom_id.category_id and 
                 ('面积' in (uom_id.category_id.name or '') or 
                  'area' in (uom_id.category_id.name or '').lower()))
            )
            
            # 调试日志
            if not is_sqm_unit:
                _logger.debug(f"[计算长度] 产品={product.name}, 单位名称={uom_name}, 单位ID={uom_id.id}, 不是平米单位")
            
            if not is_sqm_unit:
                quant.calculated_length_m = 0.0
                continue
            
            # 检查产品是否有宽度配置
            if not product_tmpl.product_width or product_tmpl.product_width <= 0:
                _logger.debug(f"[计算长度] 产品={product.name}, 没有配置宽度或宽度为0")
                quant.calculated_length_m = 0.0
                continue
            
            try:
                # 计算长度：面积(㎡) / (宽度(mm) / 1000) = 长度(m)
                # quantity 是面积（平米），product_width 是宽度（毫米）
                width_m = product_tmpl.product_width / 1000.0  # 转换为米
                if width_m > 0:
                    length_m = quant.quantity / width_m
                    quant.calculated_length_m = round(length_m, 2)
                    
                    # 调试日志
                    _logger.info(f"[计算长度] 产品={product.name}, 面积={quant.quantity}㎡, 宽度={product_tmpl.product_width}mm, 计算长度={quant.calculated_length_m}m")
                else:
                    quant.calculated_length_m = 0.0
            except (ZeroDivisionError, TypeError, ValueError) as e:
                _logger.error(f"[计算长度错误] 产品={product.name}, 错误={str(e)}", exc_info=True)
                quant.calculated_length_m = 0.0
