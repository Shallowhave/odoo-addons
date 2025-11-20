from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'
    # **关键修复**：按照扫描顺序排序，然后按照 ID 排序
    # 字段已通过 SQL 手动创建，现在可以启用排序
    _order = 'scan_sequence, id'

    # 单位数量字段
    lot_quantity = fields.Float(
        string='单位数量', 
        help='实际收到的单位数量',
        digits=(16, 2)  # 最多16位，小数点后2位
    )
    
    # 单位名称字段（动态选择）
    lot_unit_name = fields.Selection(
        selection='_get_lot_unit_name_selection',
        string='单位名称', 
        help='计量单位名称（如：桶、卷、件、箱等），根据产品配置显示可用选项'
    )
    
    # 自定义单位名称字段
    lot_unit_name_custom = fields.Char(
        string='自定义单位名称', 
        help='当选择"自定义"时填写具体的单位名称'
    )
    
    # 动态标签字段
    lot_weight_label = fields.Char(
        string='单位标签', 
        compute='_compute_lot_weight_label', 
        store=False
    )
    
    # 动态单位字段
    custom_unit_values = fields.Text(
        string='自定义单位值', 
        help='JSON格式存储的自定义单位值'
    )
    
    # 扫描顺序字段（用于保持包裹中记录的顺序）
    # **关键修复**：字段已通过 SQL 手动创建，现在可以启用
    scan_sequence = fields.Integer(
        string='扫描顺序',
        help='记录扫描顺序，用于保持包裹中记录的顺序',
        default=0,
        index=True
    )
    
    # 合同号字段（从制造订单获取）
    contract_no = fields.Char(
        string='合同号',
        help='合同号，从制造订单自动获取'
    )
    
    # 发货重量字段（根据产品发货重量系数自动计算）
    delivery_weight = fields.Float(
        string='发货重量 (kg)',
        compute='_compute_delivery_weight',
        store=False,
        digits=(16, 2),
        help='根据产品发货重量系数和面积自动计算的发货重量，单位：千克'
    )

    @api.model
    def _get_lot_unit_name_selection(self):
        """根据产品配置动态获取单位选择列表"""
        # 所有可用的单位选项
        all_options = [
            ('kg', '公斤(kg)'),
            ('roll', '卷'),
            ('barrel', '桶'),
            ('box', '箱'),
            ('bag', '袋'),
            ('sqm', '平方米(㎡)'),
            ('piece', '件'),
            ('custom', '自定义')
        ]
        
        # 尝试从当前记录获取产品
        product_id = None
        if hasattr(self, 'product_id') and self.product_id:
            product_id = self.product_id.id
        elif hasattr(self, '_origin') and self._origin and self._origin.product_id:
            product_id = self._origin.product_id.id
        else:
            # 从上下文获取产品ID（对于新建记录）
            product_id = self.env.context.get('default_product_id')
        
        if not product_id:
            return all_options
        
        # 获取产品
        product = self.env['product.product'].browse(product_id)
        if not product.exists():
            return all_options
        
        # 获取产品配置的单位
        product_tmpl = product.product_tmpl_id
        if not hasattr(product_tmpl, 'default_unit_config') or not product_tmpl.default_unit_config:
            return all_options
        
        if product_tmpl.default_unit_config == 'custom':
            return [('custom', '自定义')]
        
        # 根据产品配置返回对应的单位选项
        config_map = {
            'kg': [('kg', '公斤(kg)')],
            'roll': [('roll', '卷')],
            'barrel': [('barrel', '桶')],
            'box': [('box', '箱')],
            'bag': [('bag', '袋')],
            'sqm': [('sqm', '平方米(㎡)')],
        }
        
        return config_map.get(product_tmpl.default_unit_config, all_options)

    @api.depends('lot_unit_name', 'lot_unit_name_custom')
    def _compute_lot_weight_label(self):
        """根据选择的单位名称计算单位标签"""
        for record in self:
            if record.lot_unit_name:
                if record.lot_unit_name == 'custom':
                    record.lot_weight_label = record.lot_unit_name_custom or 'kg'
                else:
                    record.lot_weight_label = record.lot_unit_name
            else:
                record.lot_weight_label = 'kg'

    @api.depends('product_id', 'qty_done', 'quantity', 'product_id.product_tmpl_id.weight_per_sqm',
                 'product_id.product_tmpl_id.product_width', 'product_uom_id')
    def _compute_delivery_weight(self):
        """计算发货重量：根据产品发货重量系数和面积计算
        
        计算逻辑：
        1. 如果产品没有配置发货重量系数，返回 0
        2. 计算面积：
           - 如果产品单位是平方米，直接用 qty_done 或 quantity 作为面积
           - 如果产品单位不是平方米，根据 quantity 和产品宽度计算面积
        3. 计算重量：面积 × 重量系数
        """
        for record in self:
            if not record.product_id:
                record.delivery_weight = 0.0
                continue
            
            product = record.product_id
            product_tmpl = product.product_tmpl_id
            
            # 检查产品是否有发货重量系数
            if not hasattr(product_tmpl, 'weight_per_sqm') or not product_tmpl.weight_per_sqm:
                record.delivery_weight = 0.0
                continue
            
            weight_per_sqm = product_tmpl.weight_per_sqm
            
            # 获取数量（优先使用 qty_done，如果没有则使用 quantity）
            qty = record.qty_done if record.qty_done > 0 else (record.quantity or 0.0)
            if qty <= 0:
                record.delivery_weight = 0.0
                continue
            
            # 获取产品单位
            uom_id = record.product_uom_id or product.uom_id
            if not uom_id:
                record.delivery_weight = 0.0
                continue
            
            # 判断单位是否为平方米
            uom_name = str(uom_id.name or '').lower()
            is_sqm_unit = (
                '平米' in uom_name or 
                '平方米' in uom_name or 
                'sqm' in uom_name or
                'm²' in uom_name or
                'm2' in uom_name or
                (hasattr(uom_id, 'category_id') and uom_id.category_id and 
                 ('面积' in (uom_id.category_id.name or '') or 
                  'area' in (uom_id.category_id.name or '').lower()))
            )
            
            # 计算面积
            if is_sqm_unit:
                # 如果单位是平方米，直接用数量作为面积
                area_sqm = qty
            else:
                # 如果单位不是平方米，需要根据数量和产品宽度计算面积
                # 面积 = 数量 × 宽度(mm) / 1000 (转换为平方米)
                if not product_tmpl.product_width or product_tmpl.product_width <= 0:
                    record.delivery_weight = 0.0
                    continue
                
                # 假设数量单位是"米"或"卷"等，需要转换为面积
                # 如果数量单位是"米"，面积 = 数量 × 宽度(mm) / 1000
                # 如果数量单位是"卷"，需要知道每卷的面积，这里暂时按"米"处理
                # 注意：这里假设数量单位是"米"，如果是其他单位可能需要调整
                width_m = product_tmpl.product_width / 1000.0  # mm转m
                area_sqm = qty * width_m
            
            # 计算重量：面积 × 重量系数
            record.delivery_weight = round(area_sqm * weight_per_sqm, 2)

    
    @api.model_create_multi
    def create(self, vals_list):
        """在创建记录之前，验证批次号是否在预填列表中，并检查重复
       这样可以防止扫码时创建不在预填列表中的新记录，以及防止重复扫码
        
        关键逻辑：
        1. 如果当前移动中已经有已保存的记录（预填列表），新创建的记录的批次号必须在预填列表中
        2. 如果当前移动中没有已保存的记录，说明是第一次预填，允许创建任意批次号
        3. 检查当前批次创建调用中的重复（同一个 create 调用中不能有重复的批次号）
        4. 检查与已保存记录的重复（不能重复扫描已保存的批次号）
        5. 这样可以确保扫码时不能创建不在预填列表中的新记录，也不能重复扫码
        6. **关键修改**：如果记录有批次号，强制设置 quantity = 1.0（按照序列号方式）
        """
        import logging
        import traceback
        from odoo.tools import float_compare
        _logger = logging.getLogger(__name__)
        
        # **关键修改**：在创建之前，检查是否启用了增强条码验证
        # 如果启用，且有批次号，强制设置 quantity = 1.0（按照序列号方式）
        for vals in vals_list:
            lot_name = vals.get('lot_name')
            if lot_name and lot_name.strip():
                lot_name = lot_name.strip()
                move_id = vals.get('move_id')
                
                # 检查是否启用了增强条码验证
                enable_enhanced_validation = False
                if move_id:
                    try:
                        move = self.env['stock.move'].browse(move_id)
                        if move.exists() and move.picking_id and move.picking_id.picking_type_id:
                            enable_enhanced_validation = move.picking_id.picking_type_id.enable_enhanced_barcode_validation
                            _logger.info(
                                f"[批次号创建] 作业类型配置检查: move_id={move_id}, "
                                f"picking_type_id={move.picking_id.picking_type_id.id}, "
                                f"enable_enhanced_barcode_validation={enable_enhanced_validation}, "
                                f"批次号={lot_name}"
                            )
                    except (AttributeError, TypeError) as e:
                        # 配置访问错误，这是预期的
                        _logger.debug(f"[批次号创建] 检查作业类型配置时出错: {str(e)}")
                    except Exception as e:
                        # 未预期的错误
                        _logger.warning(f"[批次号创建] 检查作业类型配置时发生未预期错误: {str(e)}")
                
                # **关键修改**：只有当启用增强条码验证时，才强制设置 quantity = 1.0
                # **重要**：只对非序列号产品应用此逻辑，序列号产品保持原有逻辑
                # 完全按照序列号的方式：每个批次号对应 1.0 单位
                if enable_enhanced_validation:
                    # 检查产品不是序列号追踪（tracking != 'serial'）
                    # 序列号产品应该保持原有的 Odoo 标准逻辑
                    product_tracking = None
                    product_id = vals.get('product_id')
                    if product_id:
                        try:
                            product = self.env['product.product'].browse(product_id)
                            if product.exists():
                                product_tracking = product.tracking
                        except (AttributeError, TypeError) as e:
                            _logger.warning(f"[批次号创建] 检查产品追踪类型时出错: {str(e)}")
                    
                    # 如果无法从 vals 获取，尝试从 move 获取
                    if product_tracking is None and move_id:
                        try:
                            move = self.env['stock.move'].browse(move_id)
                            if move.exists() and move.product_id:
                                product_tracking = move.product_id.tracking
                        except (AttributeError, TypeError) as e:
                            _logger.warning(f"[批次号创建] 从移动获取产品追踪类型时出错: {str(e)}")
                    
                    # **关键修复**：只对非序列号产品应用增强验证逻辑
                    # 序列号产品（tracking == 'serial'）保持原有逻辑
                    if product_tracking != 'serial':
                        # 如果 quantity 存在且不是 1.0，强制设置为 1.0
                        if 'quantity' in vals:
                            original_quantity = vals.get('quantity', 0.0)
                            if original_quantity and float_compare(original_quantity, 1.0, precision_rounding=0.01) != 0:
                                _logger.info(
                                    f"[批次号创建] 强制设置 quantity = 1.0（启用增强验证，按照序列号方式）: 批次号={lot_name}, "
                                    f"原数量={original_quantity}, 移动ID={move_id}, 追踪类型={product_tracking}"
                                )
                                vals['quantity'] = 1.0
                        elif 'quantity' not in vals:
                            # 如果 quantity 不存在，设置默认值为 1.0（按照序列号方式）
                            _logger.info(
                                f"[批次号创建] 设置默认 quantity = 1.0（启用增强验证，按照序列号方式）: 批次号={lot_name}, "
                                f"移动ID={move_id}, 追踪类型={product_tracking}"
                            )
                            vals['quantity'] = 1.0
                        # **关键修改**：如果没有 quantity，但需要 quantity，按照序列号的方式自动设置
                        # 序列号产品在创建时，如果没有 quantity，会自动设置为 1
                        if not vals.get('quantity'):
                            vals['quantity'] = 1.0
                            _logger.info(
                                f"[批次号创建] 自动设置 quantity = 1.0（启用增强验证，按照序列号方式）: 批次号={lot_name}, "
                                f"移动ID={move_id}, 追踪类型={product_tracking}"
                            )
                    else:
                        _logger.info(
                            f"[批次号创建] 跳过增强验证（序列号产品保持原有逻辑）: 批次号={lot_name}, "
                            f"移动ID={move_id}, 追踪类型={product_tracking}"
                        )
        
        # **关键修复**：记录扫描顺序
        # 从会话变量中获取扫描顺序，并设置到新创建的记录中
        try:
            from odoo.http import request
            if request and hasattr(request, 'session'):
                session = request.session
                # 获取当前 picking_id
                picking_id = None
                for vals in vals_list:
                    move_id = vals.get('move_id')
                    if move_id:
                        try:
                            move = self.env['stock.move'].browse(move_id)
                            if move.exists() and move.picking_id:
                                picking_id = move.picking_id.id
                                break
                        except (AttributeError, TypeError):
                            pass
                
                if picking_id:
                    scanned_lots_key = f'scanned_lots_{picking_id}'
                    scanned_lots = list(session.get(scanned_lots_key, []) or [])
                    
                    if scanned_lots:
                        # 按照扫描顺序，为新创建的记录设置 scan_sequence
                        for vals in vals_list:
                            lot_name = vals.get('lot_name')
                            if lot_name and lot_name.strip():
                                lot_name_normalized = lot_name.strip().lower()
                                # 查找该批次号在扫描顺序中的位置
                                try:
                                    scan_index = scanned_lots.index(lot_name_normalized)
                                    # 设置扫描顺序（从1开始，不是从0开始）
                                    vals['scan_sequence'] = scan_index + 1
                                    _logger.info(
                                        f"[批次号创建] 设置扫描顺序: 批次号={lot_name}, "
                                        f"扫描顺序={scan_index + 1}"
                                    )
                                except ValueError:
                                    # 批次号不在扫描列表中，使用默认值 0
                                    _logger.debug(
                                        f"[批次号创建] 批次号不在扫描列表中: 批次号={lot_name}"
                                    )
        except (ImportError, AttributeError, RuntimeError) as e:
            # 无法访问 request 或没有请求上下文，跳过扫描顺序设置
            _logger.debug(f"[批次号创建] 无法访问 request，跳过扫描顺序设置: {str(e)}")
        
        # **关键修复**：从制造订单获取合同号
        # 为每个要创建的记录从制造订单获取合同号
        for vals in vals_list:
            move_id = vals.get('move_id')
            if move_id and not vals.get('contract_no'):
                try:
                    move = self.env['stock.move'].browse(move_id)
                    if move.exists():
                        # 优先从成品制造订单获取（production_id）
                        # 如果没有，则从原材料制造订单获取（raw_material_production_id）
                        contract_no = None
                        if move.production_id and move.production_id.contract_no:
                            contract_no = move.production_id.contract_no
                        elif move.raw_material_production_id and move.raw_material_production_id.contract_no:
                            contract_no = move.raw_material_production_id.contract_no
                        
                        if contract_no:
                            vals['contract_no'] = contract_no
                            _logger.info(
                                f"[合同号创建] 从制造订单获取合同号: move_id={move_id}, "
                                f"合同号={contract_no}, production_id={move.production_id.id if move.production_id else None}, "
                                f"raw_material_production_id={move.raw_material_production_id.id if move.raw_material_production_id else None}"
                            )
                except (AttributeError, TypeError) as e:
                    # 制造订单访问错误，这是预期的
                    _logger.debug(f"[合同号创建] 从制造订单获取合同号时出错: {str(e)}")
                except Exception as e:
                    # 未预期的错误
                    _logger.warning(f"[合同号创建] 从制造订单获取合同号时发生未预期错误: {str(e)}")
        
        # **关键修复**：检查是否是扫码操作
        is_barcode_scan = (
            self.env.context.get('barcode_view') or
            self.env.context.get('from_barcode') or
            'barcode' in str(self.env.context).lower() or
            self.env.context.get('list_view_ref') == 'stock.view_stock_move_line_operation_tree' or
            self.env.context.get('form_view_ref') == 'stock.view_move_line_mobile_form'
        )
        
        # **关键修复**：检查作业类型是否启用了增强条码验证
        # 只有当作业类型启用了增强条码验证时，才执行增强验证
        enable_enhanced_validation = False
        if is_barcode_scan:
            # 尝试从 vals_list 中获取 move_id，然后获取 picking_type_id
            for vals in vals_list:
                move_id = vals.get('move_id')
                if move_id:
                    try:
                        move = self.env['stock.move'].browse(move_id)
                        if move.exists() and move.picking_id and move.picking_id.picking_type_id:
                            enable_enhanced_validation = move.picking_id.picking_type_id.enable_enhanced_barcode_validation
                            _logger.info(
                                f"[扫码创建验证] 作业类型配置检查: move_id={move_id}, "
                                f"picking_type_id={move.picking_id.picking_type_id.id}, "
                                f"enable_enhanced_barcode_validation={enable_enhanced_validation}"
                            )
                            break
                    except (AttributeError, TypeError) as e:
                        _logger.warning(f"[扫码创建验证] 检查作业类型配置时出错: {str(e)}")
        
        # 如果没有启用增强验证，跳过增强验证逻辑
        if is_barcode_scan and not enable_enhanced_validation:
            _logger.info(
                f"[扫码创建验证] 作业类型未启用增强条码验证，跳过增强验证"
            )
            is_barcode_scan = False  # 将 is_barcode_scan 设为 False，跳过增强验证
        
        # 获取调用栈信息（用于调试）
        caller_info = traceback.extract_stack()[-3:-1] if len(traceback.extract_stack()) > 3 else []
        caller_str = f"{caller_info[0].filename}:{caller_info[0].lineno}" if caller_info else "unknown"
        
        _logger.info(
            f"[扫码创建验证] create 方法被调用: 要创建的记录数={len(vals_list)}, "
            f"是否来自扫码={is_barcode_scan}, "
            f"启用增强验证={enable_enhanced_validation}, "
            f"调用者={caller_str}, "
            f"context keys={list(self.env.context.keys())[:10]}, "
            f"vals_list={[(vals.get('move_id'), vals.get('lot_name'), vals.get('product_id')) for vals in vals_list]}"
        )
        
        # 收集当前创建调用中所有要创建的批次号（用于检查重复）
        current_create_lot_names = {}
        
        # 第一遍：收集所有要创建的批次号和移动ID
        for idx, vals in enumerate(vals_list):
            if not vals.get('lot_name') or not vals.get('move_id'):
                continue
            
            move_id = vals.get('move_id')
            lot_name = vals.get('lot_name')
            scanned_lot_name = lot_name.strip().lower() if lot_name else ''
            
            if not scanned_lot_name:
                continue
            
            if move_id not in current_create_lot_names:
                current_create_lot_names[move_id] = []
            current_create_lot_names[move_id].append((idx, scanned_lot_name, lot_name))
        
        _logger.info(
            f"[扫码创建验证] 收集的批次号: 移动ID -> 批次号列表 = {[(move_id, [lot_name for _, _, lot_name in lots]) for move_id, lots in current_create_lot_names.items()]}"
        )
        
        # 第二遍：检查重复和预填列表
        for vals in vals_list:
            if not vals.get('lot_name') or not vals.get('move_id'):
                continue
            
            move_id = vals.get('move_id')
            lot_name = vals.get('lot_name')
            scanned_lot_name = lot_name.strip().lower() if lot_name else ''
            product_id = vals.get('product_id')
            
            if not scanned_lot_name:
                continue
            
            _logger.info(
                f"[扫码创建验证] 验证批次号: 移动ID={move_id}, 批次号={lot_name} (标准化={scanned_lot_name}), "
                f"产品ID={product_id}"
            )
            
            # 检查当前创建调用中的重复
            if move_id in current_create_lot_names:
                duplicate_in_create = [
                    (idx, scanned_name, original_name)
                    for idx, scanned_name, original_name in current_create_lot_names[move_id]
                    if scanned_name == scanned_lot_name
                ]
                if len(duplicate_in_create) > 1:
                    # 在当前创建调用中发现了重复
                    _logger.warning(
                        f"[扫码创建验证] 阻止创建新记录: 批次号 {lot_name} 在当前创建调用中重复, "
                        f"重复次数={len(duplicate_in_create)}, 移动ID={move_id}, "
                        f"重复的索引={[idx for idx, _, _ in duplicate_in_create]}"
                    )
                    raise ValidationError(
                        _('重复扫描！\n\n'
                          '批次号 "%s" 在当前操作中重复扫描。\n\n'
                          '扫码只是验证，每个批次号只能扫描一次。\n'
                          '请勿重复扫描同一个批次号。')
                        % lot_name
                    )
            
            # 检查当前移动中是否已经有已保存的记录（预填列表）
            try:
                move = self.env['stock.move'].browse(move_id)
                if move.exists():
                    # 查询当前移动中所有已保存记录的批次号（预填列表）
                    existing_lines = self.env['stock.move.line'].search([
                        ('move_id', '=', move_id),
                        ('lot_name', '!=', False),
                        ('lot_name', '!=', ''),
                    ])
                    
                    _logger.info(
                        f"[扫码创建验证] 查询已保存记录: 移动ID={move_id}, "
                        f"已保存记录数={len(existing_lines)}, "
                        f"已保存批次号列表={[line.lot_name for line in existing_lines if line.lot_name]}"
                    )
                    
                    # **关键修复**：只有在扫码操作时，才需要检查批次号是否在预填列表中
                    # 如果是手动编辑，应该允许用户添加新批次号
                    if is_barcode_scan:
                        if existing_lines:
                            # 有已保存的记录，说明已经有预填的批次号
                            # 新创建的记录的批次号必须在预填列表中
                            existing_lot_names = [
                                line.lot_name.strip().lower() 
                                for line in existing_lines 
                                if line.lot_name
                            ]
                            
                            _logger.info(
                                f"[扫码创建验证] 预填批次号列表 (标准化): {existing_lot_names}, "
                                f"当前批次号 (标准化): {scanned_lot_name}, "
                                f"是否在预填列表中: {scanned_lot_name in existing_lot_names}"
                            )
                            
                            # **关键修复**：检查是否与已保存记录重复
                            if scanned_lot_name in existing_lot_names:
                                # 批次号在已保存记录中，说明是重复扫描，应该阻止
                                duplicate_line_ids = [
                                    line.id for line in existing_lines 
                                    if line.lot_name and line.lot_name.strip().lower() == scanned_lot_name
                                ]
                                _logger.warning(
                                    f"[扫码创建验证] 阻止创建新记录: 批次号 {lot_name} 已在已保存记录中, "
                                    f"这是重复扫描, 移动ID={move_id}, "
                                    f"重复的记录ID={duplicate_line_ids}, "
                                    f"预填批次号列表={existing_lot_names}"
                                )
                                raise ValidationError(
                                    _('重复扫描！\n\n'
                                      '批次号 "%s" 已经在已保存的记录中，请勿重复扫描！\n\n'
                                      '如需修改，请直接在列表中编辑已保存的记录。')
                                    % lot_name
                                )
                            
                            if scanned_lot_name not in existing_lot_names:
                                # 批次号不在预填列表中，应该阻止创建
                                _logger.warning(
                                    f"[扫码创建验证] 阻止创建新记录: 批次号 {lot_name} 不在预填列表中, "
                                    f"移动ID={move_id}, 产品ID={product_id}, "
                                    f"预填批次号列表={existing_lot_names}, "
                                    f"当前批次号 (标准化)={scanned_lot_name}"
                                )
                                
                                # 获取所有预填的批次号（用于显示错误消息）
                                unique_lot_names = list(set([
                                    line.lot_name 
                                    for line in existing_lines 
                                    if line.lot_name and line.lot_name.strip()
                                ]))
                                
                                raise ValidationError(
                                    _('批次号不在列表中！\n\n'
                                      '扫描的批次号："%s"\n\n'
                                      '已预填的批次号列表：\n%s\n\n'
                                      '扫码只是验证，批次号必须在预填列表中。\n'
                                      '请先手动预填批次号，然后再扫码验证。\n\n'
                                      '如需添加新批次号，请手动填写，不要使用扫码。')
                                    % (lot_name, '\n'.join(unique_lot_names))
                                )
                            else:
                                # 批次号在预填列表中，允许创建
                                _logger.info(
                                    f"[扫码创建验证] 允许创建新记录: 批次号 {lot_name} 在预填列表中, "
                                    f"移动ID={move_id}, 产品ID={product_id}"
                                )
                        else:
                            # 没有已保存的记录，说明是第一次预填，允许创建任意批次号
                            _logger.info(
                                f"[扫码创建验证] 第一次预填，允许创建批次号: {lot_name}, "
                                f"移动ID={move_id}, 产品ID={product_id}"
                            )
                    else:
                        # **关键修复**：手动编辑时，允许用户添加新批次号
                        # 不需要检查批次号是否在预填列表中
                        _logger.info(
                            f"[扫码创建验证] 手动编辑，允许创建批次号: {lot_name}, "
                            f"移动ID={move_id}, 产品ID={product_id}, "
                            f"是否来自扫码={is_barcode_scan}"
                        )
            except ValidationError:
                raise
            except (AttributeError, TypeError, ValueError) as e:
                _logger.error(
                    f"[扫码创建验证] 验证批次号时出错: 移动ID={move_id}, 批次号={lot_name}, "
                    f"错误={str(e)}", exc_info=True
                )
            except Exception as e:
                _logger.error(
                    f"[扫码创建验证] 验证批次号时发生未预期错误: 移动ID={move_id}, 批次号={lot_name}, "
                    f"错误={str(e)}", exc_info=True
                )
                # 出错时，不阻止创建，让约束验证来处理
        
        _logger.info(
            f"[扫码创建验证] 验证通过，开始创建记录: 要创建的记录数={len(vals_list)}"
        )
        
        # 调用父类的 create 方法
        result = super(StockMoveLine, self).create(vals_list)
        
        _logger.info(
            f"[扫码创建验证] 记录创建完成: 创建的记录数={len(result)}, "
            f"创建的记录ID={[r.id for r in result]}"
        )
        
        return result
    
    def write(self, vals):
        """重写 write 方法，在更新记录时验证批次号
        扫码模块可能会先创建空记录，然后通过 write 方法更新批次号
        所以需要在 write 方法中也添加验证逻辑
        6. **关键修改**：如果记录有批次号，强制设置 quantity = 1.0（按照序列号方式）
        """
        import logging
        import traceback
        from odoo.tools import float_compare
        _logger = logging.getLogger(__name__)
        
        # **关键修改**：在更新之前，检查是否启用了增强条码验证
        # 如果启用，且有批次号，强制设置 quantity = 1.0（按照序列号方式）
        enable_enhanced_validation = False
        
        # 检查是否启用了增强条码验证
        for record in self:
            if record.move_id and record.move_id.picking_id and record.move_id.picking_id.picking_type_id:
                try:
                    picking = record.move_id.picking_id
                    if picking.picking_type_id:
                        enable_enhanced_validation = picking.picking_type_id.enable_enhanced_barcode_validation
                        _logger.info(
                            f"[批次号更新] 作业类型配置检查: picking_id={picking.id}, "
                            f"picking_type_id={picking.picking_type_id.id}, "
                            f"enable_enhanced_barcode_validation={enable_enhanced_validation}"
                        )
                        break
                except (AttributeError, TypeError) as e:
                    _logger.warning(f"[批次号更新] 检查作业类型配置时出错: {str(e)}")
        
        # **关键修改**：只有当启用增强条码验证时，才强制设置 quantity = 1.0
        # **重要**：只对非序列号产品应用此逻辑，序列号产品保持原有逻辑
        if enable_enhanced_validation:
            if 'lot_name' in vals and vals.get('lot_name') and vals.get('lot_name').strip():
                # 如果更新了批次号，强制设置 quantity = 1.0
                lot_name = vals.get('lot_name').strip()
                # 检查产品不是序列号追踪（tracking != 'serial'）
                # 序列号产品应该保持原有的 Odoo 标准逻辑
                product_tracking = None
                for record in self:
                    if record.product_id:
                        product_tracking = record.product_id.tracking
                        break
                
                # **关键修复**：只对非序列号产品应用增强验证逻辑
                if product_tracking != 'serial':
                    if 'quantity' in vals:
                        original_quantity = vals.get('quantity', 0.0)
                        if original_quantity and float_compare(original_quantity, 1.0, precision_rounding=0.01) != 0:
                            _logger.info(
                                f"[批次号更新] 强制设置 quantity = 1.0（启用增强验证，按照序列号方式）: 批次号={lot_name}, "
                                f"原数量={original_quantity}, 记录ID={[r.id for r in self]}, 追踪类型={product_tracking}"
                            )
                            vals['quantity'] = 1.0
                    elif 'quantity' not in vals:
                        # 如果 quantity 不在更新列表中，也需要设置为 1.0
                        # 但需要检查现有记录是否有批次号
                        for record in self:
                            if record.lot_name and record.lot_name.strip():
                                _logger.info(
                                    f"[批次号更新] 设置 quantity = 1.0（启用增强验证，按照序列号方式）: 批次号={lot_name}, "
                                    f"记录ID={record.id}, 当前 quantity={record.quantity}, 追踪类型={product_tracking}"
                                )
                                vals['quantity'] = 1.0
                                break
                else:
                    _logger.info(
                        f"[批次号更新] 跳过增强验证（序列号产品保持原有逻辑）: 批次号={lot_name}, "
                        f"记录ID={[r.id for r in self]}, 追踪类型={product_tracking}"
                    )
            elif 'quantity' in vals:
                # 如果只更新 quantity，检查记录是否有批次号
                for record in self:
                    if record.lot_name and record.lot_name.strip() and record.product_id and record.product_id.tracking != 'serial':
                        # **关键修复**：只对非序列号产品应用增强验证逻辑
                        lot_name = record.lot_name.strip()
                        original_quantity = vals.get('quantity', 0.0)
                        if original_quantity and float_compare(original_quantity, 1.0, precision_rounding=0.01) != 0:
                            _logger.info(
                                f"[批次号更新] 强制设置 quantity = 1.0（启用增强验证，按照序列号方式）: 批次号={lot_name}, "
                                f"原数量={original_quantity}, 记录ID={record.id}, 追踪类型={record.product_id.tracking}"
                            )
                            vals['quantity'] = 1.0
                            break
        
        # **关键修复**：获取调用栈信息，以便区分扫码和手动编辑
        caller_info = traceback.extract_stack()[-4:-1] if len(traceback.extract_stack()) > 4 else []
        caller_str = ' -> '.join([f"{c.filename.split('/')[-1]}:{c.lineno}" for c in caller_info[-3:]])
        
        # **关键修复**：无论是否更新批次号，都记录日志（用于调试）
        if 'lot_name' in vals or any('lot_name' in str(v) for v in vals.values() if isinstance(v, dict)):
            _logger.info(
                f"[扫码/编辑更新验证] write 方法被调用（批次号相关）: "
                f"记录数={len(self)}, 记录ID={[r.id for r in self]}, "
                f"vals={vals}, 调用者={caller_str}, "
                f"context keys={list(self.env.context.keys())[:10]}"
            )
        
        # **关键修复**：检查是否是包裹操作
        # 包裹操作的特征：
        # 1. result_package_id 在 vals 中（即使值是 False，也可能是包裹操作）
        # 2. package_id 在 vals 中（即使值是 False，也可能是包裹操作）
        # 3. 记录已经有 result_package_id 或 package_id
        is_package_operation = False
        if 'result_package_id' in vals or 'package_id' in vals:
            is_package_operation = True
            _logger.info(
                f"[扫码/编辑更新验证] 检测到包裹操作（通过 vals）: 记录ID={[r.id for r in self]}, "
                f"result_package_id={'result_package_id' in vals}, package_id={'package_id' in vals}, "
                f"vals keys={list(vals.keys())}"
            )
        
        # **关键修复**：如果 vals 中没有包裹字段，检查记录是否已经有包裹
        # 放入包裹时，可能先设置了包裹，然后再更新批次号
        if not is_package_operation:
            for record in self:
                try:
                    if record.exists():
                        # 检查记录是否已经有 result_package_id 或 package_id
                        if (hasattr(record, 'result_package_id') and record.result_package_id) or \
                           (hasattr(record, 'package_id') and record.package_id):
                            is_package_operation = True
                            _logger.info(
                                f"[扫码/编辑更新验证] 检测到包裹操作（通过记录）: 记录ID={record.id}, "
                                f"result_package_id={record.result_package_id.id if record.result_package_id else False}, "
                                f"package_id={record.package_id.id if record.package_id else False}"
                            )
                            break
                except (AttributeError, TypeError):
                    pass
        
        # **关键修复**：从制造订单获取合同号（如果 vals 中没有合同号）
        # 如果 vals 中没有合同号，尝试从制造订单获取
        # 注意：如果 self 中有多个记录，它们可能来自不同的制造订单，需要分别处理
        if 'contract_no' not in vals:
            # 收集所有需要设置合同号的记录
            records_to_update = []
            for record in self:
                if record.exists() and record.move_id:
                    try:
                        move = record.move_id
                        # 优先从成品制造订单获取（production_id）
                        # 如果没有，则从原材料制造订单获取（raw_material_production_id）
                        contract_no = None
                        if move.production_id and move.production_id.contract_no:
                            contract_no = move.production_id.contract_no
                        elif move.raw_material_production_id and move.raw_material_production_id.contract_no:
                            contract_no = move.raw_material_production_id.contract_no
                        
                        if contract_no and contract_no != record.contract_no:
                            # 只有在合同号不同时才更新
                            records_to_update.append((record, contract_no))
                            _logger.info(
                                f"[合同号更新] 从制造订单获取合同号: record_id={record.id}, move_id={move.id}, "
                                f"合同号={contract_no}, production_id={move.production_id.id if move.production_id else None}, "
                                f"raw_material_production_id={move.raw_material_production_id.id if move.raw_material_production_id else None}"
                            )
                    except (AttributeError, TypeError) as e:
                        _logger.warning(f"[合同号更新] 从制造订单获取合同号时出错: {str(e)}")
            
            # 如果只有一个记录需要更新，且所有记录都来自同一个制造订单，则统一设置
            if records_to_update:
                # 检查所有记录是否来自同一个制造订单（同一个 move_id 或同一个制造订单）
                if len(records_to_update) == 1 or len(set([r[1] for r in records_to_update])) == 1:
                    # 所有记录都有相同的合同号，统一设置
                    vals['contract_no'] = records_to_update[0][1]
                # 如果有多个记录但合同号不同，需要在 write 后单独处理
                # 这种情况比较少见，暂时不处理
        
        # **关键修复**：在批次号验证之前，先检查每个记录是否已经有包裹
        # 这样可以提前检测到包裹操作，避免在循环中重复检测
        # 放入包裹时，可能先设置了包裹，然后再更新批次号
        if not is_package_operation:
            # 只有在 vals 中没有包裹字段时，才检查记录是否已经有包裹
            for record in self:
                try:
                    if record.exists():
                        # 检查记录是否已经有 result_package_id 或 package_id
                        if (hasattr(record, 'result_package_id') and record.result_package_id) or \
                           (hasattr(record, 'package_id') and record.package_id):
                            is_package_operation = True
                            _logger.info(
                                f"[扫码/编辑更新验证] 检测到包裹操作（记录已有包裹，在批次号验证前）: 记录ID={record.id}, "
                                f"result_package_id={record.result_package_id.id if record.result_package_id else False}, "
                                f"package_id={record.package_id.id if record.package_id else False}"
                            )
                            break
                except (AttributeError, TypeError):
                    pass
        
        # **关键修复**：如果是包裹操作，需要保持扫描顺序
        # 放入包裹时，按照扫描顺序对记录进行排序
        # **关键修复**：在包裹操作时，立即设置扫描顺序，而不是等到保存后处理
        # **关键修复**：支持多个包裹（三个、四个、五个...），每个包裹中的记录都按照扫描顺序设置
        if is_package_operation:
            # **关键修复**：如果是包裹操作，直接跳过验证
            # 放入包裹时，系统可能会先更新批次号，然后再设置包裹
            # 但如果在批次号验证时，vals 中已经有包裹字段，或者记录已经有包裹，都应该跳过验证
            # **关键修复**：在包裹操作时，立即设置扫描顺序
            _logger.info(
                f"[扫码/编辑更新验证] 包裹操作，跳过批次号验证（在验证循环前）: 记录ID={[r.id for r in self]}, "
                f"vals keys={list(vals.keys())}, 记录数={len(self)}"
            )
            
            # **关键修复**：直接调用父类方法，跳过所有批次号验证
            # 扫描顺序的设置将在 write 之后进行，这样可以确保所有记录都已保存
            result = super(StockMoveLine, self).write(vals)
            
            # **关键修复**：在 write 之后，验证 lot_quantity 和 lot_unit_name 是否正确保存
            # 如果 vals 中包含这些字段，但在 write 后被清空，需要重新设置
            if 'lot_quantity' in vals or 'lot_unit_name' in vals:
                _logger.info(
                    f"[包裹操作] write 后验证单位信息: 记录ID={[r.id for r in self]}, "
                    f"vals keys={list(vals.keys())}, lot_quantity in vals={'lot_quantity' in vals}, "
                    f"lot_unit_name in vals={'lot_unit_name' in vals}"
                )
                
                for record in self:
                    if record.exists():
                        needs_update = False
                        update_vals = {}
                        
                        # 检查 lot_quantity 是否正确保存
                        if 'lot_quantity' in vals:
                            expected_lot_quantity = vals.get('lot_quantity')
                            if expected_lot_quantity is not None:
                                # **关键修复**：直接从数据库读取，确保获取最新值
                                # 先清除缓存，然后刷新记录，最后从数据库读取
                                record.invalidate_recordset(['lot_quantity'])
                                record.env.clear_upon_failure()
                                record._cache.clear()
                                
                                # 使用 SQL 直接从数据库读取，避免缓存问题
                                try:
                                    self.env.cr.execute(
                                        "SELECT lot_quantity FROM stock_move_line WHERE id = %s",
                                        (record.id,)
                                    )
                                    db_result = self.env.cr.fetchone()
                                    actual_lot_quantity = db_result[0] if db_result and db_result[0] is not None else None
                                except Exception as e:
                                    _logger.warning(
                                        f"[包裹操作] 从数据库读取 lot_quantity 失败: 记录ID={record.id}, 错误={str(e)}"
                                    )
                                    # 如果 SQL 读取失败，使用 ORM 读取
                                    actual_lot_quantity = record.lot_quantity
                                
                                _logger.info(
                                    f"[包裹操作] write 后验证 lot_quantity: 记录ID={record.id}, "
                                    f"期望值={expected_lot_quantity}, 实际值={actual_lot_quantity}, "
                                    f"是否一致={actual_lot_quantity == expected_lot_quantity}"
                                )
                                
                                # 如果实际值与期望值不一致，或者实际值为 NULL/0，需要重新设置
                                if actual_lot_quantity != expected_lot_quantity:
                                    _logger.warning(
                                        f"[包裹操作] write 后 lot_quantity 不一致: 记录ID={record.id}, "
                                        f"期望值={expected_lot_quantity}, 实际值={actual_lot_quantity}, "
                                        f"重新设置 lot_quantity={expected_lot_quantity}"
                                    )
                                    update_vals['lot_quantity'] = expected_lot_quantity
                                    needs_update = True
                        
                        # 检查 lot_unit_name 是否正确保存
                        if 'lot_unit_name' in vals:
                            expected_lot_unit_name = vals.get('lot_unit_name')
                            if expected_lot_unit_name:
                                # **关键修复**：直接从数据库读取，确保获取最新值
                                record.invalidate_recordset(['lot_unit_name'])
                                record.env.clear_upon_failure()
                                record._cache.clear()
                                
                                # 使用 SQL 直接从数据库读取，避免缓存问题
                                try:
                                    self.env.cr.execute(
                                        "SELECT lot_unit_name FROM stock_move_line WHERE id = %s",
                                        (record.id,)
                                    )
                                    db_result = self.env.cr.fetchone()
                                    actual_lot_unit_name = db_result[0] if db_result and db_result[0] else None
                                except Exception as e:
                                    _logger.warning(
                                        f"[包裹操作] 从数据库读取 lot_unit_name 失败: 记录ID={record.id}, 错误={str(e)}"
                                    )
                                    # 如果 SQL 读取失败，使用 ORM 读取
                                    actual_lot_unit_name = record.lot_unit_name
                                
                                _logger.info(
                                    f"[包裹操作] write 后验证 lot_unit_name: 记录ID={record.id}, "
                                    f"期望值={expected_lot_unit_name}, 实际值={actual_lot_unit_name}, "
                                    f"是否一致={actual_lot_unit_name == expected_lot_unit_name}"
                                )
                                
                                if actual_lot_unit_name != expected_lot_unit_name:
                                    _logger.warning(
                                        f"[包裹操作] write 后 lot_unit_name 不一致: 记录ID={record.id}, "
                                        f"期望值={expected_lot_unit_name}, 实际值={actual_lot_unit_name}, "
                                        f"重新设置 lot_unit_name={expected_lot_unit_name}"
                                    )
                                    update_vals['lot_unit_name'] = expected_lot_unit_name
                                    needs_update = True
                                    
                                    # 如果 lot_unit_name 是 custom，也需要检查 lot_unit_name_custom
                                    if expected_lot_unit_name == 'custom' and 'lot_unit_name_custom' in vals:
                                        update_vals['lot_unit_name_custom'] = vals.get('lot_unit_name_custom')
                        
                        # 如果需要更新，重新写入
                        if needs_update:
                            try:
                                # **关键修复**：使用 ORM 方式更新，但确保清除缓存
                                # 使用 skip_quantity_fix 上下文，避免递归和数量修复
                                # 使用 sudo() 确保有足够的权限
                                record_sudo = record.sudo()
                                
                                # 清除缓存，确保获取最新值
                                record_sudo.invalidate_recordset(['lot_quantity', 'lot_unit_name', 'lot_unit_name_custom'])
                                
                                # 使用 ORM 方式更新
                                record_sudo.with_context(skip_quantity_fix=True, skip_duplicate_check=True).write(update_vals)
                                
                                # 再次清除缓存，确保后续读取获取最新值
                                record_sudo.invalidate_recordset(['lot_quantity', 'lot_unit_name', 'lot_unit_name_custom'])
                                
                                # 验证更新是否成功
                                record_sudo.env.clear_upon_failure()
                                updated_lot_quantity = record_sudo.lot_quantity
                                updated_lot_unit_name = record_sudo.lot_unit_name
                                
                                _logger.info(
                                    f"[包裹操作] write 后重新设置单位信息: 记录ID={record.id}, "
                                    f"update_vals={update_vals}, 更新后的 lot_quantity={updated_lot_quantity}, "
                                    f"更新后的 lot_unit_name={updated_lot_unit_name}"
                                )
                                
                                # 如果更新后仍然不一致，记录警告
                                if 'lot_quantity' in update_vals and updated_lot_quantity != update_vals.get('lot_quantity'):
                                    _logger.error(
                                        f"[包裹操作] write 后重新设置 lot_quantity 仍然不一致: 记录ID={record.id}, "
                                        f"期望值={update_vals.get('lot_quantity')}, 更新后的值={updated_lot_quantity}"
                                    )
                                
                                if 'lot_unit_name' in update_vals and updated_lot_unit_name != update_vals.get('lot_unit_name'):
                                    _logger.error(
                                        f"[包裹操作] write 后重新设置 lot_unit_name 仍然不一致: 记录ID={record.id}, "
                                        f"期望值={update_vals.get('lot_unit_name')}, 更新后的值={updated_lot_unit_name}"
                                    )
                            except Exception as e:
                                _logger.error(
                                    f"[包裹操作] write 后重新设置单位信息失败: 记录ID={record.id}, "
                                    f"update_vals={update_vals}, 错误={str(e)}",
                                    exc_info=True
                                )
            
            # **关键修复**：在 write 之后，为每个记录设置扫描顺序
            # 这样可以处理多个记录、多个包裹的情况，以及 vals 中没有批次号的情况
            # **关键修复**：无论有多少个包裹，都会正确处理每个包裹中的记录
            try:
                from odoo.http import request
                if request and hasattr(request, 'session'):
                    session = request.session
                    # 获取当前 picking_id
                    picking_id = None
                    for record in self:
                        if record.exists() and record.move_id and record.move_id.picking_id:
                            picking_id = record.move_id.picking_id.id
                            break
                    
                    if picking_id:
                        scanned_lots_key = f'scanned_lots_{picking_id}'
                        scanned_lots = list(session.get(scanned_lots_key, []) or [])
                        
                        _logger.info(
                            f"[包裹操作] write 后处理扫描顺序: picking_id={picking_id}, "
                            f"记录数={len(self)}, 会话中的批次号={scanned_lots}"
                        )
                        
                        if scanned_lots:
                            # **关键修复**：为每个记录设置扫描顺序
                            # 无论有多少个记录，都会为每个记录单独设置扫描顺序
                            # 这样可以支持多个包裹的情况
                            for record in self:
                                if record.exists():
                                    # 使用记录中已有的批次号（write 之后，批次号已经保存）
                                    lot_name = record.lot_name
                                    if lot_name and lot_name.strip():
                                        lot_name_normalized = lot_name.strip().lower()
                                        try:
                                            scan_index = scanned_lots.index(lot_name_normalized)
                                            # 设置扫描顺序（从1开始，不是从0开始）
                                            current_scan_sequence = record.scan_sequence or 0
                                            if current_scan_sequence != scan_index + 1:
                                                # **关键修复**：使用 skip_quantity_fix 上下文，避免递归
                                                record.with_context(skip_quantity_fix=True).write({'scan_sequence': scan_index + 1})
                                                _logger.info(
                                                    f"[包裹操作] write 后设置扫描顺序: 记录ID={record.id}, "
                                                    f"批次号={lot_name}, 扫描顺序={scan_index + 1}, "
                                                    f"包裹ID={record.result_package_id.id if record.result_package_id else False}, "
                                                    f"会话中的批次号={scanned_lots}"
                                                )
                                            else:
                                                _logger.debug(
                                                    f"[包裹操作] write 后，扫描顺序已正确: 记录ID={record.id}, "
                                                    f"批次号={lot_name}, 扫描顺序={current_scan_sequence}"
                                                )
                                        except ValueError:
                                            # 批次号不在扫描列表中，保持默认值
                                            _logger.warning(
                                                f"[包裹操作] write 后，批次号不在扫描列表中: 记录ID={record.id}, "
                                                f"批次号={lot_name}, 会话中的批次号={scanned_lots}"
                                            )
                        else:
                            _logger.warning(
                                f"[包裹操作] write 后，会话中没有批次号: picking_id={picking_id}, 记录数={len(self)}"
                            )
            except (ImportError, AttributeError, RuntimeError) as e:
                # 无法访问 request 或没有请求上下文，跳过扫描顺序设置
                _logger.debug(f"[包裹操作] write 后，无法访问 request，跳过扫描顺序设置: {str(e)}")
            except (AttributeError, TypeError, ValueError) as e:
                # 其他异常，记录日志但不阻止操作
                _logger.warning(
                    f"[包裹操作] write 后，设置扫描顺序失败: {str(e)}", 
                    exc_info=True
                )
            
            return result
        
        # 如果更新了批次号，需要验证
        if 'lot_name' in vals and vals.get('lot_name'):
            lot_name = vals.get('lot_name')
            scanned_lot_name = lot_name.strip().lower() if lot_name else ''
            
            if scanned_lot_name:
                # 对于每个要更新的记录，检查批次号
                # **关键修复**：先过滤掉不存在的记录，避免访问已删除记录导致 MissingError
                existing_records = self.exists()
                if not existing_records:
                    # 所有记录都不存在，直接跳过验证
                    _logger.warning(
                        f"[扫码/编辑更新验证] 所有记录都不存在，跳过验证: 记录ID={[r.id for r in self]}"
                    )
                    return super(StockMoveLine, self).write(vals)
                
                for record in existing_records:
                    # **关键修复**：使用 try-except 捕获 MissingError，防止访问已删除记录
                    try:
                        # 检查记录是否存在以及是否有 move_id
                        if not record.exists() or not record.move_id:
                            continue
                        
                        move_id = record.move_id.id
                        original_lot_name = record.lot_name
                        new_lot_name = lot_name
                        
                        # **关键修复**：再次检查记录是否已经有包裹（防止在循环中记录状态变化）
                        # 放入包裹时，记录可能已经有 result_package_id 或 package_id
                        record_has_package = False
                        try:
                            if (hasattr(record, 'result_package_id') and record.result_package_id) or \
                               (hasattr(record, 'package_id') and record.package_id):
                                record_has_package = True
                                # 如果记录已经有包裹，直接跳过验证
                                _logger.info(
                                    f"[扫码/编辑更新验证] 记录已有包裹，跳过批次号验证: 记录ID={record.id}, "
                                    f"result_package_id={record.result_package_id.id if record.result_package_id else False}, "
                                    f"package_id={record.package_id.id if record.package_id else False}"
                                )
                                continue
                        except (AttributeError, TypeError):
                            pass
                        
                        # **关键修复**：检查批次号是否真正变化
                        # 如果原始批次号和新批次号相同（标准化后），说明批次号没有变化
                        original_lot_name_normalized = (original_lot_name or '').strip().lower() if original_lot_name else ''
                        lot_name_really_changed = original_lot_name_normalized != scanned_lot_name
                    except (AttributeError, TypeError) as e:
                        # 记录可能已被删除或访问错误，跳过
                        _logger.warning(
                            f"[扫码/编辑更新验证] 记录访问失败，跳过验证: 记录ID={record.id}, 错误={str(e)}"
                        )
                        continue
                    
                    # **关键修复**：检查 context 中是否有扫码相关的标识
                    # 从用户的日志看，扫码时的 context 包含：
                    # - picking_type_code='incoming'
                    # - list_view_ref='stock.view_stock_move_line_operation_tree'
                    # - form_view_ref='stock.view_move_line_mobile_form'
                    is_barcode_scan = (
                        self.env.context.get('barcode_view') or
                        self.env.context.get('from_barcode') or
                        'barcode' in str(self.env.context).lower() or
                        self.env.context.get('list_view_ref') == 'stock.view_stock_move_line_operation_tree' or
                        self.env.context.get('form_view_ref') == 'stock.view_move_line_mobile_form' or
                        'barcode' in str(self.env.context.get('list_view_ref', '')).lower() or
                        'barcode' in str(self.env.context.get('form_view_ref', '')).lower()
                    )
                    
                    # **关键修复**：检查作业类型是否启用了增强条码验证
                    # 只有当作业类型启用了增强条码验证时，才执行增强验证
                    enable_enhanced_validation = False
                    if is_barcode_scan and record.move_id and record.move_id.picking_id:
                        try:
                            picking = record.move_id.picking_id
                            if picking.picking_type_id:
                                enable_enhanced_validation = picking.picking_type_id.enable_enhanced_barcode_validation
                                _logger.info(
                                    f"[扫码/编辑更新验证] 作业类型配置检查: record_id={record.id}, "
                                    f"picking_type_id={picking.picking_type_id.id}, "
                                    f"enable_enhanced_barcode_validation={enable_enhanced_validation}"
                                )
                        except (AttributeError, TypeError) as e:
                            _logger.warning(f"[扫码/编辑更新验证] 检查作业类型配置时出错: {str(e)}")
                    
                    # **关键修复**：检查产品是否是序列号追踪
                    # 序列号产品应该保持原有的 Odoo 标准逻辑，不执行增强验证
                    product_tracking = None
                    if record.product_id:
                        product_tracking = record.product_id.tracking
                    
                    # 如果没有启用增强验证，或者产品是序列号追踪，跳过增强验证逻辑
                    if is_barcode_scan and (not enable_enhanced_validation or product_tracking == 'serial'):
                        if not enable_enhanced_validation:
                            _logger.info(
                                f"[扫码/编辑更新验证] 作业类型未启用增强条码验证，跳过增强验证: record_id={record.id}"
                            )
                        elif product_tracking == 'serial':
                            _logger.info(
                                f"[扫码/编辑更新验证] 序列号产品保持原有逻辑，跳过增强验证: record_id={record.id}, "
                                f"追踪类型={product_tracking}"
                            )
                        is_barcode_scan = False  # 将 is_barcode_scan 设为 False，跳过增强验证
                    
                    _logger.info(
                        f"[扫码/编辑更新验证] write 方法被调用: 记录ID={record.id}, 移动ID={move_id}, "
                        f"原始批次号={original_lot_name}, 新批次号={new_lot_name}, "
                        f"是否来自扫码={is_barcode_scan}, "
                        f"list_view_ref={self.env.context.get('list_view_ref')}, "
                        f"form_view_ref={self.env.context.get('form_view_ref')}, "
                        f"picking_type_code={self.env.context.get('picking_type_code')}, "
                        f"context keys={list(self.env.context.keys())[:10]}, "
                        f"vals={vals}"
                    )
                    
                    # 获取调用栈信息（仅在需要时）
                    try:
                        caller_info = traceback.extract_stack()[-4:-1] if len(traceback.extract_stack()) > 4 else []
                        caller_str = ' -> '.join([f"{c.filename.split('/')[-1]}:{c.lineno}" for c in caller_info[-2:]])
                        _logger.debug(f"[扫码/编辑更新验证] 调用栈: {caller_str}")
                    except:
                        pass
                    
                    # **关键修复**：如果是扫码操作，需要执行严格的验证
                    # 如果是手动编辑，也需要验证，但逻辑可能稍有不同
                    if is_barcode_scan:
                        _logger.info(f"[扫码更新验证] 检测到扫码操作，执行严格验证")
                    
                    # **关键修复**：检查批次号是否真正变化
                    # 如果原始批次号和新批次号相同（标准化后），说明批次号没有变化
                    original_lot_name_normalized = (original_lot_name or '').strip().lower() if original_lot_name else ''
                    lot_name_changed = original_lot_name_normalized != scanned_lot_name
                    
                    _logger.info(
                        f"[扫码更新验证] 批次号变化检查: 原始批次号={original_lot_name}, "
                        f"新批次号={new_lot_name}, 批次号是否变化={lot_name_changed}, "
                        f"原始批次号(标准化)={original_lot_name_normalized}, 新批次号(标准化)={scanned_lot_name}, "
                        f"是否是包裹操作={is_package_operation}"
                    )
                    
                    # **关键修复**：如果记录已经有包裹，跳过验证
                    # 放入包裹时，记录可能已经有 result_package_id 或 package_id
                    # 这个检查已经在循环开始处做了，这里不再需要
                    
                    # **关键修复**：无论批次号是否变化，都需要验证
                    # 因为扫码时可能会：
                    # 1. 第一次扫描：从空变为批次号（lot_name_changed=True）
                    # 2. 第二次扫描：更新同一个记录，批次号相同（lot_name_changed=False），但这是重复扫描
                    # 3. 或者更新不同的记录，批次号相同（lot_name_changed=False），这也是重复扫描
                    # 所以需要检查：如果批次号在已保存记录中，且不是当前记录，说明是重复扫描
                    try:
                        move = self.env['stock.move'].browse(move_id)
                        if move.exists():
                            # 查询当前移动中所有已保存记录的批次号（预填列表）
                            # 排除当前记录
                            existing_lines = self.env['stock.move.line'].search([
                                ('move_id', '=', move_id),
                                ('lot_name', '!=', False),
                                ('lot_name', '!=', ''),
                                ('id', '!=', record.id),
                            ])
                            
                            _logger.info(
                                f"[扫码更新验证] 查询已保存记录: 移动ID={move_id}, "
                                f"已保存记录数={len(existing_lines)}, "
                                f"已保存批次号列表={[line.lot_name for line in existing_lines if line.lot_name]}"
                            )
                            
                            if existing_lines:
                                # 有已保存的记录，说明已经有预填的批次号
                                existing_lot_names = [
                                    line.lot_name.strip().lower() 
                                    for line in existing_lines 
                                    if line.lot_name
                                ]
                                
                                _logger.info(
                                    f"[扫码更新验证] 预填批次号列表 (标准化): {existing_lot_names}, "
                                    f"当前批次号 (标准化): {scanned_lot_name}, "
                                    f"是否在预填列表中: {scanned_lot_name in existing_lot_names}"
                                )
                                
                                # **关键修复**：只在扫码操作时，才检查是否与已保存记录重复
                                # 手动编辑时，允许修改批次号，即使批次号已经在其他记录中
                                # 这样用户可以手动修改批次号，而不会被误判为重复扫描
                                if is_barcode_scan and scanned_lot_name in existing_lot_names:
                                    # 扫码操作：批次号在已保存记录中，说明是重复扫描，应该阻止
                                    duplicate_line_ids = [
                                        line.id for line in existing_lines 
                                        if line.lot_name and line.lot_name.strip().lower() == scanned_lot_name
                                    ]
                                    _logger.warning(
                                        f"[扫码更新验证] 阻止更新记录: 批次号 {new_lot_name} 已在已保存记录中, "
                                        f"这是重复扫描, 记录ID={record.id}, 移动ID={move_id}, "
                                        f"重复的记录ID={duplicate_line_ids}, "
                                        f"预填批次号列表={existing_lot_names}, "
                                        f"批次号是否变化={lot_name_changed}"
                                    )
                                    raise ValidationError(
                                        _('重复扫描！\n\n'
                                          '批次号 "%s" 已经在已保存的记录中，请勿重复扫描！\n\n'
                                          '如需修改，请直接在列表中编辑已保存的记录。')
                                        % new_lot_name
                                    )
                                elif not is_barcode_scan and scanned_lot_name in existing_lot_names:
                                    # 手动编辑：批次号在已保存记录中，但这是手动编辑，允许继续
                                    # 用户可能想要修改批次号，即使批次号已经在其他记录中
                                    _logger.info(
                                        f"[手动编辑验证] 允许修改批次号: 批次号 {new_lot_name} 已在已保存记录中, "
                                        f"但这是手动编辑，允许继续, 记录ID={record.id}, 移动ID={move_id}"
                                    )
                                
                                # **关键修复**：只在扫码操作时，才检查批次号是否在预填列表中
                                # **重要**：包裹操作时，跳过"批次号不在预填列表中"的验证
                                # 手动编辑时，允许修改批次号，不检查预填列表
                                # 只在批次号发生变化时，才进行检查
                                if lot_name_changed and is_barcode_scan and not is_package_operation:
                                    # 扫码操作：检查批次号是否在预填列表中
                                    # **关键修复**：包裹操作时，跳过此验证
                                    if scanned_lot_name not in existing_lot_names:
                                        # 批次号不在预填列表中，应该阻止
                                        _logger.warning(
                                            f"[扫码更新验证] 阻止更新记录: 批次号 {new_lot_name} 不在预填列表中, "
                                            f"记录ID={record.id}, 移动ID={move_id}, 产品ID={record.product_id.id if record.product_id else None}, "
                                            f"预填批次号列表={existing_lot_names}, "
                                            f"当前批次号 (标准化)={scanned_lot_name}, "
                                            f"是否是包裹操作={is_package_operation}"
                                        )
                                        
                                        # 获取所有预填的批次号（用于显示错误消息）
                                        unique_lot_names = list(set([
                                            line.lot_name 
                                            for line in existing_lines 
                                            if line.lot_name and line.lot_name.strip()
                                        ]))
                                        
                                        raise ValidationError(
                                            _('批次号不在列表中！\n\n'
                                              '扫描的批次号："%s"\n\n'
                                              '已预填的批次号列表：\n%s\n\n'
                                              '扫码只是验证，批次号必须在预填列表中。\n'
                                              '请先手动预填批次号，然后再扫码验证。\n\n'
                                              '如需添加新批次号，请手动填写，不要使用扫码。')
                                            % (new_lot_name, '\n'.join(unique_lot_names))
                                        )
                                    else:
                                        # 批次号在预填列表中，允许更新
                                        _logger.info(
                                            f"[扫码更新验证] 允许更新记录: 批次号 {new_lot_name} 在预填列表中, "
                                            f"记录ID={record.id}, 移动ID={move_id}, 产品ID={record.product_id.id if record.product_id else None}"
                                        )
                                elif lot_name_changed and is_barcode_scan and is_package_operation:
                                    # **关键修复**：包裹操作时，即使批次号变化，也跳过"批次号不在预填列表中"的验证
                                    # 放入包裹时，可能只是更新了包裹相关字段，批次号可能是从其他地方获取的
                                    _logger.info(
                                        f"[扫码/编辑更新验证] 包裹操作，跳过批次号预填列表验证: 记录ID={record.id}, "
                                        f"原始批次号={original_lot_name}, 新批次号={new_lot_name}, "
                                        f"是否是包裹操作={is_package_operation}"
                                    )
                                    
                                    # **关键修复**：在包裹操作时，设置扫描顺序
                                    # 从会话变量中获取扫描顺序，并设置到记录中
                                    try:
                                        from odoo.http import request
                                        if request and hasattr(request, 'session'):
                                            session = request.session
                                            # 获取当前 picking_id
                                            if record.move_id and record.move_id.picking_id:
                                                picking_id = record.move_id.picking_id.id
                                                scanned_lots_key = f'scanned_lots_{picking_id}'
                                                scanned_lots = list(session.get(scanned_lots_key, []) or [])
                                                
                                                if scanned_lots and new_lot_name:
                                                    lot_name_normalized = new_lot_name.strip().lower()
                                                    try:
                                                        scan_index = scanned_lots.index(lot_name_normalized)
                                                        # 设置扫描顺序（从1开始，不是从0开始）
                                                        vals['scan_sequence'] = scan_index + 1
                                                        _logger.info(
                                                            f"[包裹操作] 设置扫描顺序: 记录ID={record.id}, "
                                                            f"批次号={new_lot_name}, 扫描顺序={scan_index + 1}, "
                                                            f"会话中的批次号={scanned_lots}"
                                                        )
                                                    except ValueError:
                                                        # 批次号不在扫描列表中，保持默认值
                                                        _logger.debug(
                                                            f"[包裹操作] 批次号不在扫描列表中: 记录ID={record.id}, "
                                                            f"批次号={new_lot_name}, 会话中的批次号={scanned_lots}"
                                                        )
                                    except (ImportError, AttributeError, RuntimeError) as e:
                                        # 无法访问 request 或没有请求上下文，跳过扫描顺序设置
                                        _logger.debug(f"[包裹操作] 无法访问 request，跳过扫描顺序设置: {str(e)}")
                                elif lot_name_changed and not is_barcode_scan:
                                    # 手动编辑：允许修改批次号，不检查预填列表
                                    # 只检查重复（已经在上面检查过了）
                                    _logger.info(
                                        f"[手动编辑验证] 允许修改批次号: 记录ID={record.id}, "
                                        f"原始批次号={original_lot_name}, 新批次号={new_lot_name}, "
                                        f"手动编辑时允许修改批次号，不检查预填列表"
                                    )
                                else:
                                    # 批次号没有变化，说明是更新其他字段，不需要检查预填列表
                                    _logger.info(
                                        f"[扫码更新验证] 批次号未变化，允许更新其他字段: 记录ID={record.id}, "
                                        f"移动ID={move_id}, 批次号={new_lot_name}"
                                    )
                            else:
                                # 没有已保存的记录，说明是第一次预填，允许更新任意批次号
                                _logger.info(
                                    f"[扫码更新验证] 第一次预填，允许更新批次号: {new_lot_name}, "
                                    f"记录ID={record.id}, 移动ID={move_id}, 产品ID={record.product_id.id if record.product_id else None}"
                                )
                    except ValidationError:
                        raise
                    except (AttributeError, TypeError, ValueError) as e:
                        _logger.error(
                            f"[扫码更新验证] 验证批次号时出错: 记录ID={record.id}, 移动ID={move_id}, 批次号={new_lot_name}, "
                            f"错误={str(e)}", exc_info=True
                        )
                    except Exception as e:
                        _logger.error(
                            f"[扫码更新验证] 验证批次号时发生未预期错误: 记录ID={record.id}, 移动ID={move_id}, 批次号={new_lot_name}, "
                            f"错误={str(e)}", exc_info=True
                        )
                        # 出错时，不阻止更新，让约束验证来处理
        
        # 调用父类的 write 方法
        result = super(StockMoveLine, self).write(vals)
        
        # **关键修复**：在调用父类 write 之后，如果启用增强条码验证，且有批次号，强制设置 quantity = 1.0
        # 因为 Odoo 的标准逻辑可能会根据 qty_done 自动更新 quantity，我们需要再次强制设置
        from odoo.tools import float_compare
        if not self.env.context.get('skip_quantity_fix'):
            # 检查是否启用了增强条码验证
            enable_enhanced_validation = False
            for record in self:
                if record.move_id and record.move_id.picking_id and record.move_id.picking_id.picking_type_id:
                    try:
                        picking = record.move_id.picking_id
                        if picking.picking_type_id:
                            enable_enhanced_validation = picking.picking_type_id.enable_enhanced_barcode_validation
                            break
                    except (AttributeError, TypeError):
                        pass
            
            # **关键修改**：只有当启用增强条码验证时，才强制设置 quantity = 1.0
            # **重要**：只对非序列号产品应用此逻辑，序列号产品保持原有逻辑
            if enable_enhanced_validation:
                for record in self:
                    # **关键修复**：只对非序列号产品应用增强验证逻辑
                    if (record.lot_name and record.lot_name.strip() and 
                        record.product_id and record.product_id.tracking != 'serial'):
                        lot_name = record.lot_name.strip()
                        # 重新读取记录，获取最新的 quantity 值
                        record.invalidate_recordset(['quantity'])
                        current_quantity = record.quantity or 0.0
                        # 检查 quantity 是否为 1.0（允许小的浮点误差）
                        if current_quantity and float_compare(current_quantity, 1.0, precision_rounding=0.01) != 0:
                            _logger.info(
                                f"[批次号更新后修复] 强制设置 quantity = 1.0（启用增强验证）: 批次号={lot_name}, "
                                f"当前 quantity={current_quantity}, 记录ID={record.id}, 追踪类型={record.product_id.tracking}"
                            )
                            # 使用 write 方法更新，但使用 context 避免递归调用
                            try:
                                record.with_context(skip_quantity_fix=True).write({'quantity': 1.0})
                                # 清除缓存，使更改立即生效
                                record.invalidate_recordset(['quantity'])
                                _logger.info(
                                    f"[批次号更新后修复] 更新成功: 批次号={lot_name}, 记录ID={record.id}"
                                )
                            except (AttributeError, TypeError, ValueError) as e:
                                _logger.error(
                                    f"[批次号更新后修复] 更新失败: 批次号={lot_name}, 记录ID={record.id}, 错误={str(e)}"
                                )
                    elif record.product_id and record.product_id.tracking == 'serial':
                        _logger.debug(
                            f"[批次号更新后修复] 跳过增强验证（序列号产品保持原有逻辑）: "
                            f"记录ID={record.id}, 追踪类型={record.product_id.tracking}"
                        )
        
        # 记录更新完成
        if 'lot_name' in vals and vals.get('lot_name'):
            _logger.info(
                f"[扫码更新验证] 记录更新完成: 更新的记录数={len(self)}, "
                f"更新的记录ID={[r.id for r in self]}"
            )
        
        return result
    
