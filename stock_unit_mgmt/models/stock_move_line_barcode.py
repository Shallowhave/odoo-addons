# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero
import logging
import re
from . import utils

_logger = logging.getLogger(__name__)

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

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
                        except (AttributeError, TypeError):
                            # _origin 不存在或没有 id 属性
                            pass
                except (AttributeError, TypeError):
                    # _origin 访问失败
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
                    except (ValueError, TypeError, AttributeError) as e:
                        # ID 解析或记录访问错误（预期的错误）
                        _logger.debug(f"[扫码入库] 通过 NewId 检测记录时出错: {str(e)}")
                    except Exception as e:
                        # 未预期的错误，记录详细信息
                        _logger.error(f"[扫码入库] 通过 NewId 检测记录时发生未预期错误: {str(e)}", exc_info=True)
                
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
                    except (AttributeError, TypeError) as e:
                        # 记录访问错误，这是预期的
                        _logger.debug(f"[库存移动编辑] 获取原始记录信息时出错: {str(e)}")
                    except Exception as e:
                        # 未预期的错误
                        _logger.warning(f"[库存移动编辑] 获取原始记录信息时发生未预期错误: {str(e)}")
                    
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
                        except (TypeError, AttributeError):
                            # 对象比较失败，这是预期的
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
                            except (TypeError, AttributeError):
                                # 对象比较失败
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
                        # 验证错误应该抛出，不要在循环中捕获
                        raise
                    except UserError:
                        # 用户错误也应该抛出
                        raise
                    except (AttributeError, TypeError, KeyError) as e:
                        # 数据访问错误，这些是非关键错误
                        _logger.debug(
                            _("[扫码入库] 处理移动行时出现数据访问错误: line_id=%s, 错误=%s"),
                            line.id if hasattr(line, 'id') else 'unknown',
                            str(e)
                        )
                        continue
                    except Exception as e:
                        # 未预期的错误，记录详细信息
                        _logger.warning(
                            _("[扫码入库] 处理移动行时发生未预期错误: line_id=%s, 错误=%s"),
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
                        except (AttributeError, TypeError) as e:
                            # 列表处理错误
                            _logger.debug(f"[扫码入库] 获取唯一批次号列表时出错: {str(e)}")
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
        except ValidationError:
            # 验证错误应该抛出，让用户看到
            raise
        except UserError:
            # 用户错误也应该抛出
            raise
        except (AttributeError, TypeError, KeyError) as e:
            # 数据访问错误，这些通常是非关键错误
            _logger.warning(
                f"[扫码入库] 验证批次号时发生数据访问错误: {str(e)}"
            )
            # 非关键错误不阻止用户操作
        except Exception as e:
            # 未预期的错误，需要详细记录
            _logger.error(
                f"[扫码入库] 验证批次号时发生未预期错误: {str(e)}", 
                exc_info=True
            )
            # 检查是否是数据完整性相关的错误
            try:
                from psycopg2 import IntegrityError
                is_integrity_error = isinstance(e, IntegrityError)
            except ImportError:
                is_integrity_error = False
            
            error_msg_lower = str(e).lower()
            if is_integrity_error or 'unique' in error_msg_lower or 'duplicate' in error_msg_lower or 'index' in error_msg_lower or '重复' in error_msg_lower:
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
            # 其他未预期错误，记录日志但不阻止用户操作
        
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
            except (AttributeError, TypeError) as e:
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
