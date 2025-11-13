# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from re import findall as regex_findall

from . import utils


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

    @api.onchange('product_id')
    def _onchange_product_id_custom_units(self):
        """当选择产品时，自动带入单位名称，但不修改原生计量单位字段
        注意：如果已经手动填写了单位信息，不会覆盖
        """
        result = super()._onchange_product_id() if hasattr(super(), '_onchange_product_id') else {}
        
        if self.product_id:
            product_tmpl = self.product_id.product_tmpl_id
            # 只在单位信息为空时才自动填充
            if not self.lot_unit_name:
                if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                    if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                        if product_tmpl.default_unit_config == 'custom':
                            self.lot_unit_name = 'custom'
                            # 自定义单位名称从产品配置中获取
                            if hasattr(product_tmpl, 'quick_unit_name') and product_tmpl.quick_unit_name:
                                self.lot_unit_name_custom = product_tmpl.quick_unit_name
                        else:
                            self.lot_unit_name = product_tmpl.default_unit_config
                    
                    elif hasattr(product_tmpl, 'custom_unit_name') and product_tmpl.custom_unit_name:
                        if product_tmpl.custom_unit_name == 'custom':
                            self.lot_unit_name = 'custom'
                            self.lot_unit_name_custom = product_tmpl.custom_unit_name_text or ''
                        else:
                            self.lot_unit_name = product_tmpl.custom_unit_name
            
            # 只在单位数量为空时才自动填充
            if not self.lot_quantity:
                if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                    if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                        self.lot_quantity = 1
                    elif hasattr(product_tmpl, 'custom_unit_value') and product_tmpl.custom_unit_value:
                        self.lot_quantity = int(product_tmpl.custom_unit_value)
                    else:
                        self.lot_quantity = 1
        
        return result

    @api.onchange('lot_unit_name', 'product_id')
    def _onchange_lot_unit_name(self):
        """当手动修改单位时，验证是否与产品配置匹配"""
        if not self.product_id or not self.lot_unit_name:
            return {}
        
        product_tmpl = self.product_id.product_tmpl_id
        if not hasattr(product_tmpl, 'enable_custom_units') or not product_tmpl.enable_custom_units:
            return {}
        
        if not hasattr(product_tmpl, 'default_unit_config') or not product_tmpl.default_unit_config:
            return {}
        
        if product_tmpl.default_unit_config != 'custom':
            if self.lot_unit_name != product_tmpl.default_unit_config:
                self.lot_unit_name = product_tmpl.default_unit_config
                return {
                    'warning': {
                        'title': '单位已自动调整',
                        'message': f'该产品已配置单位"{self._get_unit_display_name(product_tmpl.default_unit_config)}"，已自动调整为配置的单位。'
                    }
                }
        elif product_tmpl.default_unit_config == 'custom':
            custom_unit_name = product_tmpl.quick_unit_name if hasattr(product_tmpl, 'quick_unit_name') else None
            if self.lot_unit_name == 'custom' and not self.lot_unit_name_custom and custom_unit_name:
                self.lot_unit_name_custom = custom_unit_name
            elif self.lot_unit_name != 'custom':
                self.lot_unit_name = 'custom'
                if custom_unit_name:
                    self.lot_unit_name_custom = custom_unit_name
                return {
                    'warning': {
                        'title': '单位已自动调整',
                        'message': f'该产品已配置自定义单位"{custom_unit_name or "自定义"}"，已自动调整为自定义单位。'
                    }
                }
        
        return {}

    def _get_unit_display_name(self, unit_code):
        """获取单位显示名称"""
        return utils.get_unit_display_name(unit_code)

    @api.onchange('lot_name')
    def _onchange_lot_name(self):
        """当输入批次名称时，自动带入单位名称和默认值
        注意：如果用户已经手动填写了单位信息，不会覆盖
        扫码入库流程：
        1. 提前在库存移动中填好产品的批次/序列号（预填阶段）
        2. 扫码时验证批次号是否在已填写的列表中（验证阶段）
        3. 如果批次号在预填列表中，应该匹配到对应的行，而不是创建新行
        4. 如果批次号不在预填列表中，阻止创建新行
        5. 一个条码只能扫一次，重复扫会有提示
        
        关键区别：
        - 预填阶段：手动填写批次号，允许填写任意批次号，不需要验证
        - 扫码阶段：扫码验证批次号，必须在预填列表中，不允许创建新行
        """
        import logging
        _logger = logging.getLogger(__name__)
        
        if not self.lot_name or not self.product_id:
            return {}
        
        # **关键修复**：检查 context 中是否有扫码相关的标识
        is_barcode_scan = self.env.context.get('barcode_view') or \
                         self.env.context.get('from_barcode') or \
                         'barcode' in str(self.env.context).lower() or \
                         self.env.context.get('picking_type_code') == 'incoming' or \
                         'barcode' in str(self.env.context.get('list_view_ref', '')).lower() or \
                         'barcode' in str(self.env.context.get('form_view_ref', '')).lower()
        
        _logger.info(
            f"[扫码入库/编辑] _onchange_lot_name: "
            f"移动行ID={self.id}, "
            f"批次号={self.lot_name}, "
            f"产品={self.product_id.name if self.product_id else None}, "
            f"移动ID={self.move_id.id if self.move_id else None}, "
            f"是否来自扫码={is_barcode_scan}, "
            f"context keys={list(self.env.context.keys())}, "
            f"list_view_ref={self.env.context.get('list_view_ref')}, "
            f"form_view_ref={self.env.context.get('form_view_ref')}, "
            f"picking_type_code={self.env.context.get('picking_type_code')}"
        )
        
        # 验证批次号：如果当前移动（stock.move）中已经填写了批次号，扫码时必须匹配
        # 注意：如果当前行已经有 lot_id 且批次号匹配，说明是已保存的记录，不需要验证
        try:
            if self.move_id and self.move_id.move_line_ids:
                # 检查当前行是否是已保存的记录
                # 关键：在 Odoo 中，编辑已保存记录时，onchange 阶段 self.id 可能是 NewId
                # 需要使用多种方法来判断是否是已保存的记录
                is_saved_record_by_id = self.id and isinstance(self.id, int) and self.id > 0
                is_saved_record_by_origin = False
                original_record_id = None
                
                # 尝试通过 _origin 检测
                try:
                    if hasattr(self, '_origin') and self._origin:
                        try:
                            if self._origin.id:
                                is_saved_record_by_origin = True
                                original_record_id = self._origin.id
                        except Exception:
                            pass
                except Exception:
                    pass
                
                # 如果 _origin 不存在，尝试通过 NewId 的数字部分判断
                # NewId 的字符串表示可能包含数字，例如 "NewId_26190"
                is_saved_record_by_newid_numeric = False
                if not is_saved_record_by_origin and not is_saved_record_by_id:
                    try:
                        id_str = str(self.id)
                        _logger.debug(
                            f"[扫码入库] 检测 NewId: id_str={id_str}, "
                            f"type={type(self.id)}, move_id={self.move_id.id if self.move_id else None}"
                        )
                        if 'NewId_' in id_str:
                            # 提取数字部分
                            import re
                            match = re.search(r'NewId_(\d+)', id_str)
                            if match:
                                numeric_id = int(match.group(1))
                                _logger.info(
                                    f"[扫码入库] 从 NewId 提取数字ID: NewId={id_str}, 提取的数字ID={numeric_id}"
                                )
                                # 查询数据库中是否存在该记录
                                test_record = self.browse(numeric_id)
                                if test_record.exists():
                                    _logger.info(
                                        f"[扫码入库] 记录存在: ID={numeric_id}, "
                                        f"记录move_id={test_record.move_id.id if test_record.move_id else None}, "
                                        f"当前move_id={self.move_id.id if self.move_id else None}"
                                    )
                                    # 检查 move_id 是否匹配（如果 move_id 已设置）
                                    if self.move_id and test_record.move_id:
                                        if test_record.move_id.id == self.move_id.id:
                                            is_saved_record_by_newid_numeric = True
                                            original_record_id = numeric_id
                                            _logger.info(
                                                f"[扫码入库] 通过 NewId 数字部分检测到已保存记录: "
                                                f"NewId={id_str}, 实际ID={numeric_id}, move_id匹配"
                                            )
                                        else:
                                            _logger.debug(
                                                f"[扫码入库] move_id不匹配: 记录move_id={test_record.move_id.id}, "
                                                f"当前move_id={self.move_id.id}"
                                            )
                                    elif not self.move_id:
                                        # 如果当前 move_id 未设置，也认为是已保存记录（可能是编辑时的情况）
                                        is_saved_record_by_newid_numeric = True
                                        original_record_id = numeric_id
                                        _logger.info(
                                            f"[扫码入库] 通过 NewId 数字部分检测到已保存记录（move_id未设置）: "
                                            f"NewId={id_str}, 实际ID={numeric_id}"
                                        )
                                else:
                                    _logger.debug(f"[扫码入库] 记录不存在: ID={numeric_id}")
                            else:
                                _logger.debug(f"[扫码入库] 无法从 NewId 中提取数字: {id_str}")
                    except Exception as e:
                        _logger.error(f"[扫码入库] 通过 NewId 检测记录时出错: {str(e)}", exc_info=True)
                
                # 综合判断是否是已保存的记录
                is_saved_record = is_saved_record_by_id or is_saved_record_by_origin or is_saved_record_by_newid_numeric
                
                # **关键修复**：对于已保存记录，在 onchange 阶段允许修改批次号
                # 原因：
                # 1. 用户可能手动编辑已保存记录的批次号，这是正常操作，不应该被阻止
                # 2. onchange 阶段无法可靠区分"手动编辑"和"扫码"
                # 3. 真正的验证（扫码验证、重复检测、预填列表验证）应该在保存时进行（write、create、约束验证）
                # 4. 这样既能允许手动编辑，又能在保存时进行严格验证
                
                if is_saved_record:
                    # 对于已保存记录，允许修改批次号，不进行任何阻止操作
                    # 验证将在保存时进行（write 方法、约束验证）
                    try:
                        original_lot_name = None
                        if is_saved_record_by_origin:
                            original_lot_name = self._origin.lot_name if hasattr(self._origin, 'lot_name') else None
                        elif original_record_id:
                            original_record = self.browse(original_record_id)
                            if original_record.exists():
                                original_lot_name = original_record.lot_name
                        
                        _logger.info(
                            f"[库存移动编辑] 已保存记录批次号修改: 记录ID={original_record_id}, "
                            f"原始批次号={original_lot_name}, 新批次号={self.lot_name}, "
                            f"允许编辑（验证在保存时进行）"
                        )
                    except Exception as e:
                        _logger.debug(f"[库存移动编辑] 获取原始记录信息时出错: {str(e)}")
                    
                    # 不进行任何阻止操作，允许用户继续编辑
                    # 验证将在保存时进行
                    # 直接继续执行后续逻辑，不做任何拦截
                
                # 检查当前行是否是 NewId（未保存的记录）
                is_current_new = not is_saved_record
                
                # 获取当前移动中所有批次号（排除当前行）
                # 关键：需要同时检查已保存和未保存的记录
                # - 已保存的记录：用户已经预填并保存的批次号
                # - 未保存的记录（预填列表）：用户已经预填但还未保存的批次号
                # 扫码时，如果批次号匹配到预填列表，应该匹配到对应的预填行，而不是创建新行
                saved_lot_names = []  # 已保存的批次号（标准化后）
                saved_lot_lines = []  # 已保存的行记录
                pre_filled_lot_names = []  # 预填的批次号（未保存，标准化后）
                pre_filled_lot_lines = []  # 预填的行记录（未保存）
                
                # **关键修复**：在 onchange 阶段，需要获取所有记录（包括未保存的 NewId 记录）
                # 不能使用 exists()，因为 exists() 会过滤掉 NewId 记录
                # 我们需要检查所有记录（已保存和未保存）来检测重复
                move_lines = self.move_id.move_line_ids
                
                scanned_lot_name = self.lot_name.strip().lower() if self.lot_name else ''
                
                for line in move_lines:
                    # **关键修复**：正确识别并排除当前行
                    # 问题：Odoo的扫码逻辑可能在onchange之前就已经修改了数据，导致我们无法准确识别"当前行"
                    # 解决方案：使用多种方法来判断是否是当前行
                    is_current_line = False
                    
                    # 方法1：比较ID（对于已保存的记录）
                    if is_saved_record and hasattr(line, 'id') and isinstance(line.id, int) and line.id > 0:
                        is_current_line = (line.id == self.id)
                    # 方法2：比较对象身份（对于未保存的记录）
                    elif not is_saved_record:
                        # 对于NewId记录，比较对象身份
                        try:
                            is_current_line = (line is self)
                        except Exception:
                            # 如果对象身份比较失败，尝试其他方法
                            pass
                    
                    # 方法3：比较批次号（如果批次号相同，且是同一个移动，可能是当前行）
                    # 注意：这种方法不够精确，因为可能有多个行有相同的批次号
                    # 但作为最后的判断依据
                    if not is_current_line and line.lot_name and scanned_lot_name:
                        line_lot_normalized = line.lot_name.strip().lower() if line.lot_name else ''
                        if line_lot_normalized == scanned_lot_name:
                            # 如果批次号相同，且当前行是NewId，且另一个行也是NewId，可能是Odoo创建的新行
                            # 这种情况下，我们需要更谨慎地判断
                            # 暂时不将其视为当前行，因为可能是真正的重复
                            pass
                    
                    # 跳过当前行和没有批次号的行
                    if is_current_line or not line.lot_name:
                        continue
                    
                    try:
                        lot_name_normalized = line.lot_name.strip().lower() if line.lot_name else ''
                        if not lot_name_normalized:
                            continue
                        
                        # **关键修复**：如果批次号与当前行相同，且当前行是NewId，跳过（可能是Odoo创建的新行，或者是当前行本身）
                        if lot_name_normalized == scanned_lot_name and is_current_new:
                            # 检查是否是同一个对象（对于NewId记录）
                            try:
                                if line is self:
                                    continue
                            except Exception:
                                pass
                            # 如果不是同一个对象，但批次号相同，可能是Odoo创建的新行
                            # 这种情况下，我们需要检查：如果当前移动中已经存在相同批次号的行，说明是重复扫码
                            # 但问题是我们无法确定这是"当前行"还是"其他行"
                            # 为了安全起见，我们暂时不跳过，让后续的重复检测逻辑处理
                        
                        # 检查是否是已保存的记录（有真实 ID）
                        is_line_saved = hasattr(line, 'id') and isinstance(line.id, int) and line.id > 0
                        
                        if is_line_saved:
                            # 已保存的记录
                            saved_lot_names.append(lot_name_normalized)
                            saved_lot_lines.append(line)
                        else:
                            # 未保存的记录（预填列表）
                            # 关键：需要检查预填列表，防止扫码时创建重复的新行
                            pre_filled_lot_names.append(lot_name_normalized)
                            pre_filled_lot_lines.append(line)
                    except ValidationError:
                        # 验证错误应该抛出，不捕获
                        raise
                    except UserError:
                        # 用户错误应该抛出，不捕获
                        raise
                    except Exception as e:
                        # 其他错误记录日志，但不阻止操作（非关键错误）
                        _logger.warning(
                            _("[扫码入库] 处理移动行时出错: line_id=%s, 错误=%s"),
                            line.id if hasattr(line, 'id') else 'unknown',
                            str(e),
                            exc_info=True
                        )
                        continue
                
                _logger.debug(
                    _("[扫码入库] 验证批次号: 当前行ID=%s, 是否NewId=%s, 批次号=%s (标准化=%s), "
                      "已保存的批次号列表=%s, 预填的批次号列表=%s, 移动行总数=%s"),
                    self.id, is_current_new, self.lot_name, scanned_lot_name,
                    saved_lot_names, pre_filled_lot_names, len(move_lines)
                )
                
                # **根本性修复**：在 onchange 阶段，完全移除所有验证逻辑
                # 原因：
                # 1. 无法可靠区分"手动填写"和"扫码"
                # 2. 任何在 onchange 阶段的验证都会误判手动填写为扫码
                # 3. 用户反复反馈：手动填写时也会触发"重复批次号"提示
                # 4. 解决方案：onchange 阶段只做数据同步（如匹配 lot_id），不做任何验证
                # 5. 所有验证（重复检测、预填列表验证）都在约束验证阶段进行（保存时）
                
                # **重要**：onchange 阶段只检查与已保存记录的重复（防止重复扫描已保存的记录）
                # 这是唯一安全的检查，因为已保存的记录是明确的
                # 不检查预填列表中的重复，因为无法区分手动填写和扫码
                if is_current_new:
                    # 只检查是否与已保存的记录重复
                    # 如果批次号已经在已保存的记录中，说明是重复扫描已保存的记录，应该阻止
                    if scanned_lot_name and scanned_lot_name in saved_lot_names:
                        # 重复：批次号已经在已保存的记录中，说明是重复扫描，应该阻止
                        _logger.warning(
                            f"[扫码入库] 重复批次号: 批次号 {self.lot_name} 已在已保存的记录中，"
                            f"不允许重复扫描，清空批次号以阻止创建新行"
                        )
                        
                        # 保存原始批次号用于显示警告消息
                        original_lot_name = self.lot_name
                        
                        # 清空批次号，阻止创建新行
                        self.lot_name = False
                        self.lot_id = False
                        
                        # 获取唯一批次号列表用于显示
                        try:
                            unique_lot_names = list(set([
                                line.lot_name 
                                for line in saved_lot_lines 
                                if line.lot_name
                            ]))
                        except Exception as e:
                            _logger.error(f"[扫码入库] 获取唯一批次号列表时出错: {str(e)}")
                            unique_lot_names = saved_lot_names
                        
                        message = f'批次号 "{original_lot_name}" 已经在已保存的记录中，不允许重复扫描！\n\n'
                        message += f'该批次号已存在于已保存的记录中。\n\n'
                        message += f'扫码只是验证，不允许创建新记录。\n\n'
                        message += f'如需修改，请直接在列表中编辑已保存的记录。'
                        
                        return {
                            'warning': {
                                'title': '重复批次号',
                                'message': message
                            },
                            'value': {
                                'lot_name': False,
                                'lot_id': False,
                            }
                        }
                    
                    # **关键修复**：在 onchange 阶段，不再检查预填列表中的重复
                    # 原因：
                    # 1. 无法可靠区分"手动填写"和"扫码"
                    # 2. 手动填写时，如果批次号在预填列表中（即使是同一个记录），也会被误判为重复
                    # 3. 对象身份比较（`is`）在某些情况下会失败，导致误判
                    # 4. 所有验证（包括重复检测和预填列表验证）都在约束验证阶段进行（保存时）
                    # 5. 这样可以确保手动填写时不会被误判为重复扫码
                    
                    _logger.info(
                        f"[扫码入库] 允许填写批次号: {self.lot_name} "
                        f"(当前行是NewId；所有验证在保存时进行，不会在 onchange 阶段阻止手动填写)"
                    )
        except Exception as e:
            _logger.error(
                f"[扫码入库] 验证批次号时发生错误: {str(e)}", 
                exc_info=True
            )
            # 发生错误时，如果是重复检测相关的错误，应该清空批次号以防止重复保存
            # 但如果是其他错误（如数据访问错误），则不阻止用户操作
            error_msg = str(e).lower()
            if 'index' in error_msg or '重复' in error_msg or 'duplicate' in error_msg:
                # 重复检测相关的错误，清空批次号以防止重复保存
                _logger.warning(
                    f"[扫码入库] 检测到重复批次号相关错误，清空批次号以防止重复保存: {self.lot_name}"
                )
                original_lot_name = self.lot_name
                self.lot_name = False
                self.lot_id = False
                return {
                    'warning': {
                        'title': '批次号验证错误',
                        'message': f'验证批次号时发生错误，已自动清空批次号 "{original_lot_name}"。\n\n'
                                 f'请检查批次号是否重复，或联系管理员。'
                    },
                    'value': {
                        'lot_name': False,
                        'lot_id': False,
                    }
                }
            # 其他错误不阻止用户操作，只记录日志
        
        # 只在单位名称为空时才自动填充
        if not self.lot_unit_name:
            if hasattr(self.product_id.product_tmpl_id, 'custom_unit_name') and self.product_id.product_tmpl_id.custom_unit_name:
                if self.product_id.product_tmpl_id.custom_unit_name == 'custom':
                    self.lot_unit_name = self.product_id.product_tmpl_id.custom_unit_name_text or ''
                else:
                    self.lot_unit_name = self.product_id.product_tmpl_id.custom_unit_name
        
        # 只在单位数量为空时才自动填充
        if not self.lot_quantity:
            if hasattr(self.product_id.product_tmpl_id, 'custom_unit_value') and self.product_id.product_tmpl_id.custom_unit_value:
                self.lot_quantity = int(self.product_id.product_tmpl_id.custom_unit_value)
            else:
                self.lot_quantity = 1
        
        return {}
    
    @api.onchange('quantity', 'product_id', 'product_uom_id', 'lot_name')
    def _onchange_quantity(self):
        """重写数量变更方法
        
        **关键修改**：当启用增强条码验证时，按照序列号的方式处理批次号
        - 如果启用增强条码验证，且有批次号（lot_name），必须 quantity = 1.0（完全按照序列号逻辑）
        - 如果 quantity 不是 1.0，抛出错误（和序列号一样）
        - 序列号产品：如果配置了自定义单位，允许部分数量
        - 批次号产品（启用增强验证）：每个批次号对应 1.0 单位，不允许部分数量，必须抛出错误
        """
        import logging
        from odoo.tools.float_utils import float_compare, float_is_zero
        from odoo.exceptions import UserError
        _logger = logging.getLogger(__name__)
        
        # **关键修改**：检查是否启用了增强条码验证
        # 只有当启用增强条码验证时，才按照序列号的方式处理批次号
        enable_enhanced_validation = False
        if self.lot_name and self.lot_name.strip() and self.move_id and self.move_id.picking_id:
            try:
                picking = self.move_id.picking_id
                if picking.picking_type_id:
                    enable_enhanced_validation = picking.picking_type_id.enable_enhanced_barcode_validation
                    _logger.info(
                        f"[批次号数量验证] 作业类型配置检查: picking_id={picking.id}, "
                        f"picking_type_id={picking.picking_type_id.id}, "
                        f"enable_enhanced_barcode_validation={enable_enhanced_validation}"
                    )
            except Exception as e:
                _logger.warning(f"[批次号数量验证] 检查作业类型配置时出错: {str(e)}")
        
        # **关键修改**：如果启用增强条码验证，且有批次号，按照序列号的方式处理
        # **重要**：只对非序列号产品应用此逻辑，序列号产品保持原有逻辑
        # 完全按照 Odoo 标准序列号的逻辑：quantity 必须是 1.0，否则抛出错误
        if (enable_enhanced_validation and self.lot_name and self.lot_name.strip() and 
            self.quantity and self.product_uom_id and 
            self.product_id and self.product_id.tracking != 'serial'):
            # 批次号按照序列号的方式处理，每个批次号对应 1.0 单位
            # 但只对非序列号产品应用此逻辑
            precision = self.product_uom_id.rounding or 0.01
            quantity_uom = self.quantity_product_uom if hasattr(self, 'quantity_product_uom') else self.quantity
            
            # **关键修改**：完全按照序列号的逻辑，检查 quantity 是否为 1.0
            # 如果 quantity 不是 1.0，抛出错误（而不是自动设置）
            if float_compare(quantity_uom, 1.0, precision_rounding=precision) != 0 and not float_is_zero(quantity_uom, precision_rounding=precision):
                # 数量不是 1.0，抛出错误（和序列号一样）
                product_uom_name = self.product_id.uom_id.name if self.product_id and self.product_id.uom_id else '单位'
                _logger.warning(
                    f"[批次号数量验证] 批次号产品数量必须是 1.0: "
                    f"批次号={self.lot_name}, 当前数量={quantity_uom}, "
                    f"产品={self.product_id.name if self.product_id else None}, "
                    f"追踪类型={self.product_id.tracking if self.product_id else 'N/A'}"
                )
                raise UserError(_('您只能处理 1.0 %s 具有批次号的产品（启用增强条码验证时，批次号按照序列号方式处理）。') % product_uom_name)
        
        # 如果没有启用增强验证，或者没有批次号，继续处理序列号产品的逻辑
        
        # 只在产品使用序列号追踪时进行自定义检查
        if self.product_id and self.product_id.tracking == 'serial' and self.quantity and self.product_uom_id:
            # 检查产品是否配置了自定义单位
            product_tmpl = self.product_id.product_tmpl_id
            has_custom_units = False
            
            # 检查是否有自定义单位配置
            if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                    has_custom_units = True
                    _logger.info(
                        f"[序列号数量验证] 产品 {self.product_id.name} 配置了自定义单位: "
                        f"{product_tmpl.default_unit_config}, 允许部分数量"
                    )
            
            # 如果产品配置了自定义单位，允许数量小于1.0（但仍需大于0）
            if has_custom_units:
                # 获取产品的UOM精度
                precision = self.product_id.uom_id.rounding
                quantity_uom = self.quantity_product_uom
                
                # 检查数量是否大于0
                if float_compare(quantity_uom, 0.0, precision_rounding=precision) <= 0:
                    # 数量小于等于0，不允许
                    _logger.warning(
                        f"[序列号数量验证] 产品 {self.product_id.name} 的数量必须大于0: "
                        f"quantity_uom={quantity_uom}"
                    )
                    return {
                        'warning': {
                            'title': '数量错误',
                            'message': f'产品 "{self.product_id.name}" 的数量必须大于0。\n\n'
                                     f'当前数量：{quantity_uom} {self.product_id.uom_id.name}'
                        }
                    }
                
                # 允许部分数量（小于1.0），不调用父类方法（避免抛出错误）
                _logger.info(
                    f"[序列号数量验证] 产品 {self.product_id.name} 配置了自定义单位，"
                    f"允许部分数量: quantity_uom={quantity_uom}"
                )
                # 不调用父类方法，直接返回（避免父类方法抛出错误）
                return {}
        
        # 如果没有配置自定义单位，或者不是序列号追踪，调用父类的原始逻辑
        try:
            result = super()._onchange_quantity() if hasattr(super(), '_onchange_quantity') else {}
            return result
        except UserError as e:
            # 如果父类方法抛出了 UserError，检查是否是序列号数量验证错误
            error_msg = str(e)
            if 'You can only process 1.0' in error_msg or '只能处理 1.0' in error_msg:
                # 这是序列号数量验证错误，检查是否配置了自定义单位
                if self.product_id and self.product_id.tracking == 'serial':
                    product_tmpl = self.product_id.product_tmpl_id
                    if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                        if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                            # 配置了自定义单位，允许部分数量，不抛出错误
                            _logger.info(
                                f"[序列号数量验证] 产品 {self.product_id.name} 配置了自定义单位，"
                                f"允许部分数量，忽略父类验证错误"
                            )
                            return {}
            # 其他错误，重新抛出
            raise
    
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
                    except Exception as e:
                        _logger.warning(f"[批次号创建] 检查作业类型配置时出错: {str(e)}")
                
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
                        except Exception as e:
                            _logger.warning(f"[批次号创建] 检查产品追踪类型时出错: {str(e)}")
                    
                    # 如果无法从 vals 获取，尝试从 move 获取
                    if product_tracking is None and move_id:
                        try:
                            move = self.env['stock.move'].browse(move_id)
                            if move.exists() and move.product_id:
                                product_tracking = move.product_id.tracking
                        except Exception as e:
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
                        except Exception:
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
                except Exception as e:
                    _logger.warning(f"[合同号创建] 从制造订单获取合同号时出错: {str(e)}")
        
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
                    except Exception as e:
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
            except Exception as e:
                _logger.error(
                    f"[扫码创建验证] 验证批次号时出错: 移动ID={move_id}, 批次号={lot_name}, "
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
                except Exception as e:
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
                except Exception:
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
                    except Exception as e:
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
                except Exception:
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
            except Exception as e:
                # 其他异常，记录日志但不阻止操作
                _logger.warning(
                    f"[包裹操作] write 后，设置扫描顺序时出错: {str(e)}", 
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
                        except Exception:
                            pass
                        
                        # **关键修复**：检查批次号是否真正变化
                        # 如果原始批次号和新批次号相同（标准化后），说明批次号没有变化
                        original_lot_name_normalized = (original_lot_name or '').strip().lower() if original_lot_name else ''
                        lot_name_really_changed = original_lot_name_normalized != scanned_lot_name
                    except Exception as e:
                        # 记录可能已被删除，跳过
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
                        except Exception as e:
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
                    except Exception as e:
                        _logger.error(
                            f"[扫码更新验证] 验证批次号时出错: 记录ID={record.id}, 移动ID={move_id}, 批次号={new_lot_name}, "
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
                    except Exception:
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
                            except Exception as e:
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
    
    @api.constrains('lot_quantity')
    def _check_lot_quantity(self):
        """验证单位数量不能为负数"""
        for record in self:
            if record.lot_quantity and record.lot_quantity < 0:
                raise ValidationError(_('单位数量不能为负数！'))
    
    @api.constrains('lot_name')
    def _check_lot_name_length(self):
        """验证批次号长度"""
        for record in self:
            if record.lot_name and len(record.lot_name) > 255:
                raise ValidationError(_('批次号长度不能超过255个字符！'))
    
    @api.constrains('lot_name', 'move_id')
    def _check_lot_name_match(self):
        """验证扫码的批次号必须与已填写的批次号匹配，且不能重复扫描
        扫码入库流程：
        1. 提前在库存移动中填好产品的批次/序列号
        2. 扫码时验证批次号是否在已填写的列表中
        3. 一个条码只能扫一次，重复扫会阻止保存
        """
        import logging
        _logger = logging.getLogger(__name__)
        
        # **关键修复**：先过滤掉不存在的记录，避免访问已删除记录导致 MissingError
        existing_records = self.exists()
        if not existing_records:
            # 所有记录都不存在，直接跳过验证
            return
        
        for record in existing_records:
            # **关键修复**：使用 try-except 捕获 MissingError，防止访问已删除记录
            try:
                # 检查记录是否存在以及是否有 lot_name 和 move_id
                if not record.exists():
                    continue
                
                # 安全地访问字段
                lot_name = record.lot_name
                move_id_record = record.move_id
                
                if not lot_name or not move_id_record:
                    continue
                
                move_id = move_id_record.id
            except Exception as e:
                # 记录可能已被删除，跳过
                _logger.warning(
                    f"[批次号约束验证] 记录访问失败，跳过验证: 记录ID={record.id}, 错误={str(e)}"
                )
                continue
            
            _logger.info(
                f"[批次号约束验证] 记录ID={record.id}, "
                f"批次号={lot_name}, "
                f"移动ID={move_id}"
            )
            
            # 关键逻辑：
            # 1. 如果当前记录是已保存的记录，且批次号没有变化，只需要检查重复，不需要检查"批次号不在列表中"
            # 2. 如果当前记录是已保存的记录，但批次号发生了变化，需要检查新批次号是否在已保存的记录列表中
            # 3. 如果当前记录是新的记录（未保存），需要检查批次号是否在已保存的记录列表中（扫码验证）
            # 4. 永远检查重复，确保每个批次号在当前移动中只出现一次
            
            # 检查当前记录是否是已保存的记录，以及批次号是否发生变化
            is_saved_record = record.id and isinstance(record.id, int) and record.id > 0
            lot_name_changed = False
            original_lot_name = None
            
            if is_saved_record:
                # **关键修复**：从数据库读取原始批次号，而不是从 _origin 获取
                # 因为 _origin 在约束验证阶段可能不可用或不准确
                # 从数据库读取是最可靠的方式
                try:
                    # 从数据库读取原始批次号
                    db_record = self.env['stock.move.line'].browse(record.id)
                    if db_record.exists():
                        original_lot_name = db_record.lot_name
                        current_lot_name = record.lot_name
                        # 标准化比较，确保比较的准确性
                        original_lot_normalized = original_lot_name.strip().lower() if original_lot_name else ''
                        current_lot_normalized = current_lot_name.strip().lower() if current_lot_name else ''
                        lot_name_changed = original_lot_normalized != current_lot_normalized
                        _logger.info(
                            f"[批次号约束验证] 已保存记录，从数据库读取原始批次号={original_lot_name}, "
                            f"当前批次号={current_lot_name}, 批次号是否变化={lot_name_changed}"
                        )
                    else:
                        # 记录不存在于数据库中，可能是新记录
                        _logger.warning(f"[批次号约束验证] 记录ID={record.id} 不存在于数据库中，可能是新记录")
                        lot_name_changed = False
                except Exception as e:
                    _logger.warning(f"[批次号约束验证] 从数据库读取原始批次号时出错: {str(e)}")
                    # 出错时，尝试使用 _origin
                    try:
                        if hasattr(record, '_origin') and record._origin:
                            original_lot_name = record._origin.lot_name
                            current_lot_name = record.lot_name
                            lot_name_changed = original_lot_name != current_lot_name
                            _logger.info(
                                f"[批次号约束验证] 已保存记录，从 _origin 获取原始批次号={original_lot_name}, "
                                f"当前批次号={current_lot_name}, 批次号是否变化={lot_name_changed}"
                            )
                    except Exception as e2:
                        _logger.warning(f"[批次号约束验证] 从 _origin 获取原始批次号时也出错: {str(e2)}")
                        # 出错时，假设批次号可能发生变化，继续验证
                        lot_name_changed = True  # 为了安全起见，假设批次号可能发生了变化
            
            # 获取当前移动中所有记录（排除当前行）
            # 关键：需要同时检查已保存和未保存的记录，因为在批量保存时，其他记录可能还未保存
            saved_lot_names = []  # 已保存的批次号（标准化后）
            saved_lot_lines = []  # 已保存的行记录
            unsaved_lot_names = []  # 未保存的批次号（标准化后，用于重复检测和预填验证）
            unsaved_lot_lines = []  # 未保存的行记录（用于获取原始批次号）
            duplicate_lines = []  # 重复的批次号行（包括已保存和未保存的）
            
            scanned_lot_name = record.lot_name.strip().lower() if record.lot_name else ''
            
            # **关键修复**：在批量保存时，需要获取所有记录（包括未保存的NewId记录）
            # 不能使用 exists()，因为 exists() 会过滤掉 NewId 记录
            # 我们需要检查所有记录（已保存和未保存）来检测重复
            # **关键修复**：安全地访问 move_id.move_line_ids，防止访问已删除记录导致 MissingError
            try:
                move_line_ids = record.move_id.move_line_ids
            except Exception as e:
                # move_id 或 move_line_ids 访问失败，可能是记录已被删除
                _logger.warning(
                    f"[批次号约束验证] 访问 move_line_ids 失败，跳过验证: 记录ID={record.id}, "
                    f"move_id={move_id}, 错误={str(e)}"
                )
                continue
            
            # 注意：在约束验证阶段，move_line_ids 可能包含 NewId 记录
            # 我们需要手动遍历所有记录，不能使用 exists() 过滤
            # 但是，我们需要确保只处理有效的记录（有批次号的记录）
            # **关键修复**：使用 exists() 过滤掉已删除的记录，避免访问已删除记录导致 MissingError
            try:
                existing_move_line_ids = move_line_ids.exists()
            except Exception as e:
                _logger.warning(
                    f"[批次号约束验证] 过滤已删除记录失败，跳过验证: 记录ID={record.id}, "
                    f"move_id={move_id}, 错误={str(e)}"
                )
                continue
            
            for line in existing_move_line_ids:
                # 跳过当前行和没有批次号的行
                # 注意：在批量保存时，当前记录可能在 move_line_ids 中
                # 我们需要使用更精确的检查来排除当前记录
                # **关键修复**：使用 try-except 捕获所有可能的异常，防止访问已删除记录
                try:
                    # 首先检查记录是否存在
                    if not line.exists():
                        continue
                    
                    # 检查是否是当前记录
                    # 如果 record.id 是 NewId，需要比较对象的身份
                    is_current_line = False
                    if is_saved_record:
                        # 如果是已保存的记录，直接比较 ID
                        try:
                            line_id = line.id if hasattr(line, 'id') else None
                            is_current_line = (isinstance(line_id, int) and line_id > 0 and line_id == record.id)
                        except Exception:
                            # 访问 line.id 失败，跳过
                            continue
                    else:
                        # 如果是未保存的记录，比较对象的身份（内存地址）
                        # 注意：在批量保存时，可能无法直接比较对象身份
                        # 因此，我们使用其他方式来判断：如果批次号相同且是同一个移动，则可能是当前记录
                        # 但这种方法不够精确，所以我们暂时跳过批次号相同的未保存记录（在重复检测时会处理）
                        is_current_line = (line is record)
                    
                    # 安全地访问 line.lot_name
                    try:
                        line_lot_name = line.lot_name
                    except Exception:
                        # 访问 line.lot_name 失败，跳过
                        continue
                    
                    if is_current_line or not line_lot_name:
                        continue
                    
                    lot_name_normalized = line_lot_name.strip().lower() if line_lot_name else ''
                    if not lot_name_normalized:
                        continue
                    
                    # 检查是否是已保存的记录（有真实 ID）
                    try:
                        line_id = line.id if hasattr(line, 'id') else None
                        is_saved = isinstance(line_id, int) and line_id > 0
                    except Exception:
                        # 访问 line.id 失败，假设是未保存的记录
                        is_saved = False
                    
                    if is_saved:
                        # 已保存的记录
                        saved_lot_lines.append(line)
                        saved_lot_names.append(lot_name_normalized)
                    else:
                        # 未保存的记录（NewId），也添加到列表中进行重复检测和预填验证
                        # 这是为了检测批量保存时的重复批次号和预填验证
                        unsaved_lot_lines.append(line)
                        unsaved_lot_names.append(lot_name_normalized)
                    
                    # 检查是否是重复的批次号（与当前记录的批次号相同）
                    # 注意：在批量保存时，当前记录可能也在 move_line_ids 中
                    # 我们需要确保不会把当前记录自己当作重复
                    if lot_name_normalized == scanned_lot_name:
                        # 检查是否是当前记录
                        if is_saved_record:
                            # 如果是已保存的记录，检查 ID 是否相同
                            try:
                                line_id = line.id if hasattr(line, 'id') else None
                                if isinstance(line_id, int) and line_id > 0:
                                    if line_id != record.id:
                                        duplicate_lines.append(line)
                                else:
                                    # 未保存的记录，可能重复
                                    duplicate_lines.append(line)
                            except Exception:
                                # 访问 line.id 失败，跳过
                                continue
                        else:
                            # 如果是未保存的记录，检查是否是同一个对象
                            if line is not record:
                                duplicate_lines.append(line)
                except Exception as e:
                    _logger.warning(
                        f"[批次号约束验证] 处理移动行时出错: "
                        f"line_id={line.id if hasattr(line, 'id') else 'unknown'}, "
                        f"错误={str(e)}"
                    )
                    continue
            
            # **关键修复**：在批量保存时，如果没有已保存的记录，但有未保存的记录（预填列表）
            # 需要检查当前记录的批次号是否在预填列表中
            # 这样可以防止用户预填了批次号A和B，但扫码输入了批次号C的情况
            all_prefilled_lot_names = saved_lot_names + unsaved_lot_names  # 所有预填的批次号（已保存+未保存）
            
            _logger.info(
                f"[批次号约束验证] 批次号={record.lot_name} (标准化={scanned_lot_name}), "
                f"已保存的批次号列表={saved_lot_names}, "
                f"未保存的批次号列表={unsaved_lot_names}, "
                f"所有预填批次号列表={all_prefilled_lot_names}, "
                f"已保存的记录数={len(saved_lot_lines)}, "
                f"未保存的记录数={len(unsaved_lot_lines)}, "
                f"重复批次号行数={len(duplicate_lines)}"
            )
            
            # 关键逻辑：
            # 1. 永远检查重复，确保每个批次号在当前移动中只出现一次
            # 2. 如果当前记录是已保存的记录，且批次号没有变化，只需要检查重复，不需要检查"批次号不在列表中"
            # 3. 如果当前记录是已保存的记录，但批次号发生了变化，需要检查新批次号是否在预填列表中（已保存+未保存）
            # 4. 如果当前记录是新的记录（未保存），需要检查批次号是否在预填列表中（已保存+未保存）（扫码验证）
            # 5. **关键修复**：如果存在预填列表（已保存或未保存），扫码的批次号必须在预填列表中
            
            # **关键修复**：检查重复批次号（包括已保存和未保存的记录）
            # 关键逻辑：
            # 1. 检查是否与已保存的记录重复
            # 2. 检查是否与未保存的记录重复（批量保存时的重复检测）
            # 3. 使用 duplicate_lines 列表，因为它已经包含了所有重复的记录
            # 4. 注意：如果是同一个记录（自己），不算重复
            
            # 检查是否是当前记录自己（如果是自己，不算重复）
            is_self_duplicate = False
            if is_saved_record:
                # 如果是已保存的记录，检查是否是自己的批次号
                try:
                    for line in saved_lot_lines:
                        try:
                            if line.exists() and line.id == record.id:
                                line_lot_name = line.lot_name
                                if line_lot_name and line_lot_name.strip().lower() == scanned_lot_name:
                                    is_self_duplicate = True
                                    break
                        except Exception:
                            # 访问 line 失败，跳过
                            continue
                except Exception:
                    # 访问 saved_lot_lines 失败，跳过
                    pass
            else:
                # 如果是未保存的记录，检查是否是同一个对象
                # duplicate_lines 中可能包含当前记录，需要排除
                try:
                    for line in duplicate_lines:
                        try:
                            if line is record:
                                is_self_duplicate = True
                                break
                        except Exception:
                            # 访问 line 失败，跳过
                            continue
                except Exception:
                    # 访问 duplicate_lines 失败，跳过
                    pass
            
            # 如果有重复的记录，且不是当前记录自己，则阻止保存
            if duplicate_lines and not is_self_duplicate:
                # 过滤掉当前记录自己
                real_duplicate_lines = []
                for line in duplicate_lines:
                    try:
                        # 检查记录是否存在
                        if not line.exists():
                            continue
                        
                        if is_saved_record:
                            # 如果是已保存的记录，检查 ID 是否相同
                            try:
                                line_id = line.id if hasattr(line, 'id') else None
                                if isinstance(line_id, int) and line_id > 0:
                                    if line_id != record.id:
                                        real_duplicate_lines.append(line)
                                else:
                                    # 未保存的记录，可能重复
                                    real_duplicate_lines.append(line)
                            except Exception:
                                # 访问 line.id 失败，跳过
                                continue
                        else:
                            # 如果是未保存的记录，检查是否是同一个对象
                            if line is not record:
                                real_duplicate_lines.append(line)
                    except Exception:
                        # 访问 line 失败，跳过
                        continue
                
                if real_duplicate_lines:
                    duplicate_line_ids = []
                    duplicate_new_ids = []
                    for l in real_duplicate_lines:
                        try:
                            if l.exists():
                                if hasattr(l, 'id') and isinstance(l.id, int) and l.id > 0:
                                    duplicate_line_ids.append(l.id)
                                else:
                                    duplicate_new_ids.append(str(l.id))
                        except Exception:
                            continue
                    
                    # 检查是否与已保存的记录重复
                    has_saved_duplicate = False
                    for l in real_duplicate_lines:
                        try:
                            if l.exists() and hasattr(l, 'id') and isinstance(l.id, int) and l.id > 0:
                                has_saved_duplicate = True
                                break
                        except Exception:
                            continue
                    
                    # 检查是否与未保存的记录重复
                    has_unsaved_duplicate = False
                    for l in real_duplicate_lines:
                        try:
                            if l.exists() and not (hasattr(l, 'id') and isinstance(l.id, int) and l.id > 0):
                                has_unsaved_duplicate = True
                                break
                        except Exception:
                            continue
                    
                    _logger.warning(
                        f"[批次号约束验证] 重复批次号: 批次号 {record.lot_name} 已重复, "
                        f"重复行ID={duplicate_line_ids}, 重复新记录={duplicate_new_ids}, "
                        f"与已保存记录重复={has_saved_duplicate}, 与未保存记录重复={has_unsaved_duplicate}"
                    )
                    
                    if has_saved_duplicate:
                        raise ValidationError(
                            _('重复扫描！\n\n'
                              '批次号 "%s" 已经在已保存的记录中，请勿重复扫描！\n\n'
                              '如需修改，请直接在列表中编辑已保存的记录。')
                            % record.lot_name
                        )
                    elif has_unsaved_duplicate:
                        raise ValidationError(
                            _('重复扫描！\n\n'
                              '批次号 "%s" 已经在当前待保存的记录中，请勿重复扫描！\n\n'
                              '同一个批次号只能扫描一次。')
                            % record.lot_name
                        )
                    else:
                        raise ValidationError(
                            _('重复扫描！\n\n'
                              '批次号 "%s" 已重复，请勿重复扫描！\n\n'
                              '同一个批次号只能扫描一次。')
                            % record.lot_name
                        )
            
            # **关键修复**：问题的核心是：用户预填了批次号1和2，但扫码了123和321，这些不应该被保存
            # 解决方案：我们需要确定"最初预填的批次号列表"
            # 关键逻辑：
            # 1. 如果当前移动中已经存在已保存的记录（这些是预填的批次号）
            # 2. 那么，任何新记录或修改的记录的批次号必须在这些已保存记录的批次号列表中
            # 3. 但是，我们需要排除当前记录本身，以及可能被错误保存的扫码记录
            
            # **更好的解决方案**：
            # 1. 获取当前移动中**所有已保存记录**的批次号（排除当前记录）
            # 2. 如果这些已保存记录的批次号列表不为空，说明已经有预填的批次号
            # 3. 那么，当前记录的批次号必须在这个列表中
            # 4. 但是，问题是：如果用户扫码了123和321，这些记录也会被保存，导致列表包含123和321
            # 5. **关键**：我们需要在**第一次保存时**就阻止不匹配的批次号
            
            # **实际可行的解决方案**：
            # 1. 在约束验证中，检查：如果当前移动中已经存在其他已保存的记录
            # 2. 那么，当前记录的批次号必须在这些已保存记录的批次号列表中
            # 3. 这样可以确保：一旦有预填的批次号保存后，后续的批次号必须匹配
            
            # **关键修复**：在批量保存时，需要合并已保存和未保存的批次号列表
            # 问题：当批量保存时，第一个记录保存后，第二个记录的约束验证会查询数据库
            # 数据库查询时，只查询到第一个记录的批次号，没有包含第二个记录（因为第二个记录还未保存）
            # 第二个记录的批次号不在查询结果中，被误判为"不在预填列表中"
            # 解决方案：合并已保存和未保存的批次号列表，作为完整的预填列表
            
            # 从数据库中获取当前移动的所有已保存记录的批次号（排除当前记录）
            # 这些就是"预填列表"（已保存的批次号）
            db_prefilled_lot_names = []
            has_db_saved_records = False  # 标记是否有已保存的记录
            if record.move_id and record.move_id.id:
                try:
                    # 查询当前移动的所有已保存记录的批次号（排除当前记录）
                    # 使用 search 确保只获取真实存在的记录
                    domain = [
                        ('move_id', '=', record.move_id.id),
                        ('lot_name', '!=', False),
                        ('lot_name', '!=', ''),
                    ]
                    if is_saved_record:
                        domain.append(('id', '!=', record.id))
                    
                    db_move_lines = self.env['stock.move.line'].search(domain)
                    
                    # 获取所有批次号（标准化后）
                    for db_line in db_move_lines:
                        if db_line.lot_name:
                            db_lot_name_normalized = db_line.lot_name.strip().lower()
                            if db_lot_name_normalized and db_lot_name_normalized not in db_prefilled_lot_names:
                                db_prefilled_lot_names.append(db_lot_name_normalized)
                                has_db_saved_records = True  # 标记有已保存的记录
                    
                    _logger.info(
                        f"[批次号约束验证] 从数据库获取已保存批次号列表（预填列表）: {db_prefilled_lot_names}, "
                        f"当前记录批次号={record.lot_name} (标准化={scanned_lot_name}), "
                        f"是否有已保存记录={has_db_saved_records}"
                    )
                except Exception as e:
                    _logger.warning(f"[批次号约束验证] 从数据库获取预填批次号列表时出错: {str(e)}")
                    # 出错时，尝试使用内存中的已保存记录
                    if saved_lot_names:
                        db_prefilled_lot_names = saved_lot_names
                        has_db_saved_records = True
            
            # **关键修复**：合并已保存和未保存的批次号列表，作为完整的预填列表
            # 在批量保存时，未保存的记录（unsaved_lot_names）也应该是预填列表的一部分
            # 这样可以正确处理批量保存的场景
            all_prefilled_lot_names_combined = list(set(db_prefilled_lot_names + unsaved_lot_names))
            
            _logger.info(
                f"[批次号约束验证] 合并预填列表: "
                f"数据库预填批次号列表={db_prefilled_lot_names}, "
                f"未保存批次号列表={unsaved_lot_names}, "
                f"合并后的预填列表={all_prefilled_lot_names_combined}"
            )
            
            # **关键修复**：只有在有已保存的记录时，才需要检查"批次号不在列表中"
            # 关键逻辑：
            # 1. **最重要**：如果记录是已保存的，且批次号发生了变化，这是用户的手动编辑，应该允许修改，不需要检查批次号是否在预填列表中
            # 2. 如果有已保存的记录（has_db_saved_records=True），说明已经有预填的批次号
            # 3. 在批量保存时，需要检查当前记录的批次号是否在合并后的预填列表中
            # 4. 但是，如果记录是已保存的，且批次号没有变化，且批次号在合并后的预填列表中，应该允许保存
            # 5. 如果记录是新的（未保存），且批次号在合并后的预填列表中，应该允许保存
            # 6. **关键**：只有在记录是新的（未保存），且批次号不在合并后的预填列表中时，才应该阻止
            #    因为手动预填时，批次号应该在预填列表中（已保存或未保存）
            
            # **关键修复**：如果记录是已保存的，且批次号发生了变化，这是用户的手动编辑
            # 应该允许修改，不需要检查批次号是否在预填列表中
            # 直接跳过所有预填列表检查，只检查重复
            if is_saved_record and lot_name_changed:
                _logger.info(
                    f"[批次号约束验证] 已保存记录，批次号发生了变化（从 {original_lot_name} 改为 {record.lot_name}），"
                    f"这是用户的手动编辑，允许保存，不需要检查批次号是否在预填列表中"
                )
                # 跳过所有预填列表检查，允许保存
                # 但需要检查重复（上面已经检查过了）
            elif is_saved_record and not lot_name_changed:
                # **关键修复**：已保存的记录，批次号没有变化
                # 这种情况可能是：
                # 1. 用户手动编辑了批次号，write 方法已经更新了数据库，约束验证时从数据库读取的已经是新值
                # 2. 约束验证时检测到批次号没有变化（因为数据库中的值已经是新值了）
                # 3. 但是，如果批次号不在预填列表中，可能是手动编辑的结果，应该允许保存
                # 
                # **解决方案**：对于已保存的记录，如果批次号没有变化
                # 说明记录已经被保存过了，不需要再检查批次号是否在预填列表中
                # 只检查重复即可
                # 
                # **关键**：即使批次号不在预填列表中，只要是已保存的记录，就应该允许保存
                # 因为记录已经被保存过了，说明是手动编辑的结果
                _logger.info(
                    f"[批次号约束验证] 已保存记录，批次号未变化（批次号={record.lot_name}），"
                    f"记录已被保存过，允许保存，不需要检查批次号是否在预填列表中"
                )
                # 跳过所有预填列表检查，允许保存
                # 但需要检查重复（上面已经检查过了）
                # 使用 continue 跳过后续的预填列表检查
                continue
            elif has_db_saved_records and all_prefilled_lot_names_combined:
                # **关键修复**：检查当前记录的批次号是否在合并后的预填列表中
                # 如果当前记录的批次号不在合并后的预填列表中，说明是扫码时输入了不在预填列表中的批次号，应该阻止
                if scanned_lot_name not in all_prefilled_lot_names_combined:
                    # 批次号不在合并后的预填列表中
                    # **关键修复**：对于已保存的记录，即使批次号不在预填列表中，也应该允许保存
                    # 因为记录已经被保存过了，说明是手动编辑的结果
                    # 约束验证不应该阻止已保存记录的保存操作
                    if is_saved_record:
                        # **关键修复**：对于已保存的记录，即使批次号不在预填列表中，也应该允许保存
                        # 因为记录已经被保存过了，说明是手动编辑的结果
                        # 约束验证不应该阻止已保存记录的保存操作
                        _logger.info(
                            f"[批次号约束验证] 已保存记录，批次号不在预填列表中，但记录已被保存过，允许保存: {record.lot_name}"
                        )
                        # 跳过预填列表检查，允许保存
                        # 但需要检查重复（上面已经检查过了）
                        continue
                    else:
                        # 新记录（未保存），批次号不在合并后的预填列表中，应该阻止
                        # 这是扫码时输入了不在预填列表中的批次号
                        _logger.warning(
                            f"[批次号约束验证] 新记录，批次号不在预填列表中: "
                            f"记录ID={record.id}, 批次号={record.lot_name}, "
                            f"数据库预填批次号列表={db_prefilled_lot_names}"
                        )
                        
                        # 获取所有预填的批次号（用于显示错误消息）
                        try:
                            db_lines = self.env['stock.move.line'].search([
                                ('move_id', '=', record.move_id.id),
                                ('lot_name', '!=', False),
                                ('lot_name', '!=', ''),
                            ])
                            unique_lot_names = list(set([
                                db_line.lot_name 
                                for db_line in db_lines
                                if db_line.lot_name and db_line.lot_name.strip()
                            ]))
                        except Exception:
                            unique_lot_names = db_prefilled_lot_names
                        
                        raise ValidationError(
                            _('批次号不在列表中！\n\n'
                              '扫描的批次号："%s"\n\n'
                              '已预填的批次号列表：\n%s\n\n'
                              '扫码只是验证，批次号必须在预填列表中。\n'
                              '请先手动预填批次号，然后再扫码验证。')
                            % (record.lot_name, '\n'.join(unique_lot_names))
                        )
                else:
                    # 批次号在合并后的预填列表中，允许保存
                    # 如果记录是已保存的，且批次号没有变化，说明是合法的预填记录
                    if is_saved_record and not lot_name_changed:
                        _logger.info(
                            f"[批次号约束验证] 已保存记录，批次号未变化，且在合并后的预填列表中，允许保存: {record.lot_name}"
                        )
                    else:
                        _logger.info(
                            f"[批次号约束验证] 批次号在合并后的预填列表中，允许保存: {record.lot_name}"
                        )
            else:
                # 没有已保存的记录，说明是第一次预填，允许保存
                # 但是，我们已经检查了重复（上面已经检查过了）
                _logger.info(
                    f"[批次号约束验证] 没有已保存的记录（has_db_saved_records={has_db_saved_records}），"
                    f"这是第一次预填，允许保存: {record.lot_name}"
                )
