# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
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
    
    # 原膜产品专用字段：实际米数
    actual_length_m = fields.Float(
        string='实际米数 (m)',
        compute='_compute_roll_dimensions',
        store=True,
        digits=(16, 2),
        help='原膜产品的实际米数（根据卷数和每卷长度计算，或根据面积和宽度计算）'
    )
    
    # 原膜产品专用字段：实际平方
    actual_area_sqm = fields.Float(
        string='实际平方 (㎡)',
        compute='_compute_roll_dimensions',
        store=True,
        digits=(16, 2),
        help='原膜产品的实际平方（根据卷数和每卷面积计算，或直接使用库存数量）'
    )
    
    # 计算字段：是否为原膜产品（用于视图显示控制）
    is_roll_product = fields.Boolean(
        string='是否原膜产品',
        compute='_compute_is_roll_product',
        help='用于控制原膜产品专用字段的显示'
    )

    def _get_move_line_base_quantity_for_lot_unit(self, move_line):
        """Fallback quantity for continuous extra units when lot_quantity is empty."""
        for field_name in ('quantity', 'qty_done'):
            if field_name in move_line._fields:
                value = move_line[field_name] or 0.0
                if value > 0:
                    return value
        return 0.0

    def _sum_move_line_done_quantities(self, move_lines):
        """Sum completed quantities with Odoo 18 quantity / legacy qty_done compatibility."""
        field_name = (
            'quantity' if 'quantity' in move_lines._fields
            else 'qty_done' if 'qty_done' in move_lines._fields
            else False
        )
        if not field_name:
            return 0.0
        return sum(move_lines.mapped(field_name) or [0.0])

    def _get_move_line_lot_unit_quantity(self, move_line):
        """Return the extra-unit quantity represented by a done move line."""
        if move_line.lot_quantity and move_line.lot_quantity > 0:
            return move_line.lot_quantity
        if not move_line.lot_unit_name:
            return 0.0
        if utils.should_default_quantity_to_one(
            move_line.lot_unit_name,
            move_line.lot_unit_name_custom,
        ):
            return 1.0
        return self._get_move_line_base_quantity_for_lot_unit(move_line)

    @api.depends('lot_id', 'product_id', 'quantity', 'location_id', 'package_id', 'owner_id', 'company_id')
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
        domain = [
            ('lot_id', 'in', lot_ids),
            ('product_id', 'in', product_ids),
            ('state', '=', 'done'),
        ]
        company_ids = self.filtered('company_id').mapped('company_id').ids
        if company_ids:
            domain.append(('company_id', 'in', company_ids))
        all_move_lines = self.env['stock.move.line'].search(domain)
        
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
                if quant.company_id:
                    relevant_move_lines = relevant_move_lines.filtered(
                        lambda ml: not ml.company_id or ml.company_id == quant.company_id
                    )
                if quant.owner_id:
                    relevant_move_lines = relevant_move_lines.filtered(lambda ml: ml.owner_id == quant.owner_id)
                else:
                    relevant_move_lines = relevant_move_lines.filtered(lambda ml: not ml.owner_id)

                # 筛选入库和出库移动行
                # 注意：入库是指 destination 是当前 quant 的位置，出库是指 source 是当前 quant 的位置
                # 直接使用位置ID匹配（因为 stock.quant 的位置应该是精确的）
                incoming_move_lines = relevant_move_lines.filtered(
                    lambda ml: ml.location_dest_id.id == quant.location_id.id
                    and ml.result_package_id == quant.package_id
                )
                incoming_with_lot_qty = incoming_move_lines.filtered(
                    lambda ml: ml.lot_quantity and ml.lot_quantity > 0
                )
                
                outgoing_move_lines = relevant_move_lines.filtered(
                    lambda ml: ml.location_id.id == quant.location_id.id
                    and ml.package_id == quant.package_id
                )
                
                # 累加入库的单位数量。卷/箱/袋等计数单位可默认 1；
                # 米/㎡/kg 等连续单位回退到移动行实际数量。
                total_incoming = 0.0
                for ml in incoming_move_lines:
                    lot_unit_quantity = self._get_move_line_lot_unit_quantity(ml)
                    if lot_unit_quantity:
                        total_incoming += lot_unit_quantity
                    elif ml.lot_unit_name:
                        _logger.debug(
                            f"[批次数量计算] 移动行 {ml.id} 有单位名称但无法取得单位数量: "
                            f"lot_unit_name={ml.lot_unit_name}, "
                            f"lot_unit_name_custom={ml.lot_unit_name_custom}, "
                            f"lot_id={ml.lot_id.id if ml.lot_id else None}"
                        )
                
                # 累加出库的单位数量，规则同入库。
                total_outgoing = 0.0
                for ml in outgoing_move_lines:
                    lot_unit_quantity = self._get_move_line_lot_unit_quantity(ml)
                    if lot_unit_quantity:
                        total_outgoing += lot_unit_quantity
                    elif ml.lot_unit_name:
                        _logger.debug(
                            f"[批次数量计算] 移动行 {ml.id} 有单位名称但无法取得单位数量: "
                            f"lot_unit_name={ml.lot_unit_name}, "
                            f"lot_unit_name_custom={ml.lot_unit_name_custom}, "
                            f"lot_id={ml.lot_id.id if ml.lot_id else None}"
                        )
                
                # 计算当前剩余的单位数量
                current_lot_quantity = total_incoming - total_outgoing
                
                # 调试日志：记录详细信息，特别是当没有单位信息时
                product_code = quant.product_id.default_code or quant.product_id.name
                lot_name = quant.lot_id.name if quant.lot_id else 'None'
                # 检查是否缺少单位信息（在计算完成后检查）
                # 使用配置参数控制调试日志（生产环境默认关闭）
                enable_debug_logging = self.env['ir.config_parameter'].sudo().get_param(
                    'stock_unit_mgmt.enable_debug_logging', 'False'
                ).lower() == 'true'
                should_log = enable_debug_logging and (
                    (not current_lot_quantity or current_lot_quantity <= 0) and quant.quantity > 0
                )
                
                if should_log:
                    # 记录详细信息用于调试（使用 DEBUG 级别）
                    incoming_with_lot_qty_count = len([ml for ml in incoming_move_lines if ml.lot_quantity and ml.lot_quantity > 0])
                    incoming_with_unit_name_count = len([ml for ml in incoming_move_lines if ml.lot_unit_name])
                    _logger.debug(
                        _("[批次数量计算] 产品=%s, 批次=%s, 位置=%s, 位置ID=%s, "
                          "库存数量=%s, 所有移动行数=%s, 入库移动行数=%s, "
                          "有数量入库行数=%s, 有单位名称入库行数=%s, 总入库数量=%s, 总出库数量=%s, 计算出的单位数量=%s"),
                        product_code, lot_name,
                        quant.location_id.name if quant.location_id else 'None',
                        quant.location_id.id if quant.location_id else 'None',
                        quant.quantity, len(relevant_move_lines),
                        len(incoming_move_lines), incoming_with_lot_qty_count,
                        incoming_with_unit_name_count, total_incoming, total_outgoing, current_lot_quantity
                    )
                    # 记录移动行详情
                    if relevant_move_lines:
                        for ml in relevant_move_lines[:5]:  # 记录前5条
                            _logger.debug(
                                _("  -> 移动行 %s: lot_quantity=%s, lot_unit_name=%s, "
                                  "location_dest=%s(ID:%s), location_id=%s(ID:%s), state=%s"),
                                ml.id, ml.lot_quantity, ml.lot_unit_name,
                                ml.location_dest_id.name if ml.location_dest_id else 'None',
                                ml.location_dest_id.id if ml.location_dest_id else 'None',
                                ml.location_id.name if ml.location_id else 'None',
                                ml.location_id.id if ml.location_id else 'None',
                                ml.state
                            )
                    else:
                        _logger.debug(_("  -> 没有找到相关的移动行"))
                
                # 详细调试日志（仅在启用详细日志时输出）
                if self.env['ir.config_parameter'].sudo().get_param('stock_unit_mgmt.enable_debug_logging', 'False').lower() == 'true':
                    incoming_with_lot_qty_count = len([ml for ml in incoming_move_lines if ml.lot_quantity and ml.lot_quantity > 0])
                    incoming_details = [
                        (ml.id, self._get_move_line_lot_unit_quantity(ml))
                        for ml in incoming_move_lines
                    ]
                    _logger.debug(f"[批次数量计算] 批次={quant.lot_id.name if quant.lot_id else 'None'}, "
                                 f"位置={quant.location_id.name if quant.location_id else 'None'}, "
                                 f"所有移动行数={len(relevant_move_lines)}, "
                                 f"入库移动行数={len(incoming_move_lines)}, "
                                 f"有数量入库行数={incoming_with_lot_qty_count}, "
                                 f"总入库数量={total_incoming}, "
                                 f"总出库数量={total_outgoing}")
                
                # 如果还有库存但单位数量为0或负数，说明可能出库时没有填写单位数量
                # 在这种情况下，按比例计算
                if quant.quantity > 0 and current_lot_quantity <= 0 and total_incoming > 0:
                    # 找到总的入库数量
                    total_incoming_qty = self._sum_move_line_done_quantities(incoming_move_lines)
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
                
                # **关键修复**：从移动行获取合同号
                # 优先从入库移动行获取，如果没有则从出库移动行获取
                if incoming_move_lines:
                    # 从入库移动行获取合同号（优先最新的）
                    latest_incoming = incoming_move_lines.sorted(key='id', reverse=True)[:1]
                    if latest_incoming and latest_incoming.contract_no:
                        quant.contract_no = latest_incoming.contract_no
                    elif relevant_move_lines:
                        # 如果入库移动行没有合同号，尝试从所有移动行获取
                        latest_all = relevant_move_lines.sorted(key='id', reverse=True)[:1]
                        if latest_all and latest_all.contract_no:
                            quant.contract_no = latest_all.contract_no
                        else:
                            # 如果所有移动行都没有合同号，保持原值或设为 False
                            pass
                elif relevant_move_lines:
                    # 如果没有入库移动行，尝试从所有移动行获取
                    latest_all = relevant_move_lines.sorted(key='id', reverse=True)[:1]
                    if latest_all and latest_all.contract_no:
                        quant.contract_no = latest_all.contract_no
                
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
    
    @api.depends('quantity', 'product_id', 
                 'product_id.product_tmpl_id.categ_id',
                 'product_id.product_tmpl_id.product_width',
                 'product_id.product_tmpl_id.product_length')
    def _compute_roll_dimensions(self):
        """计算原膜产品的实际米数和实际平方
        
        适用条件：产品类别为原膜（categ_id 对应的类别名称包含"原膜"）
        
        计算规则：根据产品的产品属性中的宽度(mm)和长度(m)，以及产品的数量计算
        
        计算公式：
        - 实际米数 = 数量 × 长度(m)
        - 实际平方 = 数量 × 宽度(mm) / 1000 × 长度(m)
        """
        for quant in self:
            if not quant.product_id:
                quant.actual_length_m = 0.0
                quant.actual_area_sqm = 0.0
                continue
            
            product = quant.product_id
            product_tmpl = product.product_tmpl_id
            
            # 检查产品类别是否为原膜
            if not product_tmpl.categ_id:
                quant.actual_length_m = 0.0
                quant.actual_area_sqm = 0.0
                continue
            
            # 判断类别名称是否包含"原膜"
            category_name = product_tmpl.categ_id.name or ''
            is_roll_category = '原膜' in category_name
            
            if not is_roll_category:
                quant.actual_length_m = 0.0
                quant.actual_area_sqm = 0.0
                continue
            
            try:
                # 获取产品属性：宽度(mm)和长度(m)
                product_width = product_tmpl.product_width or 0.0
                product_length = product_tmpl.product_length or 0.0
                
                if not product_width or not product_length:
                    _logger.warning(
                        f"[原膜计算] 产品={product.name}, 缺少产品属性: "
                        f"宽度={product_width}mm, 长度={product_length}m"
                    )
                    quant.actual_length_m = 0.0
                    quant.actual_area_sqm = 0.0
                    continue
                
                # 对于原膜产品，直接根据数量、宽度和长度计算
                # 实际米数 = 数量 × 长度(m)
                quant.actual_length_m = round(quant.quantity * product_length, 2)
                
                # 实际平方 = 数量 × 宽度(mm) / 1000 × 长度(m)
                width_m = product_width / 1000.0
                quant.actual_area_sqm = round(quant.quantity * width_m * product_length, 2)
                
                _logger.info(
                    f"[原膜计算] 产品={product.name}, 类别={category_name}, 数量={quant.quantity}, "
                    f"宽度={product_width}mm, 长度={product_length}m, "
                    f"实际米数={quant.actual_length_m}m, 实际平方={quant.actual_area_sqm}㎡"
                )
                        
            except Exception as e:
                _logger.warning(
                    f"[原膜计算] 计算失败: 产品={product.name}, 错误={str(e)}",
                    exc_info=True
                )
                quant.actual_length_m = 0.0
                quant.actual_area_sqm = 0.0
    
    @api.depends('product_id', 'product_id.product_tmpl_id.categ_id')
    def _compute_is_roll_product(self):
        """计算是否为原膜产品（用于视图显示控制）
        
        判断条件：产品类别名称包含"原膜"
        """
        for quant in self:
            if not quant.product_id or not quant.product_id.product_tmpl_id:
                quant.is_roll_product = False
                continue
            
            product_tmpl = quant.product_id.product_tmpl_id
            
            # 根据产品类别判断是否为原膜产品
            if not product_tmpl.categ_id:
                quant.is_roll_product = False
                continue
            
            category_name = product_tmpl.categ_id.name or ''
            quant.is_roll_product = '原膜' in category_name
