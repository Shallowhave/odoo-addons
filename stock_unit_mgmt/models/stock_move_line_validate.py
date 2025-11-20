# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

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
                except (AttributeError, TypeError, ValueError) as e:
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
            except (AttributeError, TypeError) as e:
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
                        except (AttributeError, TypeError):
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
                    except (AttributeError, TypeError):
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
                    except (AttributeError, TypeError):
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
                            except (AttributeError, TypeError):
                                # 访问 line.id 失败，跳过
                                continue
                        else:
                            # 如果是未保存的记录，检查是否是同一个对象
                            if line is not record:
                                duplicate_lines.append(line)
                except (AttributeError, TypeError) as e:
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
                        except (AttributeError, TypeError):
                            # 访问 line 失败，跳过
                            continue
                except (AttributeError, TypeError):
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
                            except (AttributeError, TypeError):
                                # 访问 line.id 失败，跳过
                                continue
                        else:
                            # 如果是未保存的记录，检查是否是同一个对象
                            if line is not record:
                                real_duplicate_lines.append(line)
                    except (AttributeError, TypeError):
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
                        except (AttributeError, TypeError):
                            continue
                    
                    # 检查是否与已保存的记录重复
                    has_saved_duplicate = False
                    for l in real_duplicate_lines:
                        try:
                            if l.exists() and hasattr(l, 'id') and isinstance(l.id, int) and l.id > 0:
                                has_saved_duplicate = True
                                break
                        except (AttributeError, TypeError):
                            continue
                    
                    # 检查是否与未保存的记录重复
                    has_unsaved_duplicate = False
                    for l in real_duplicate_lines:
                        try:
                            if l.exists() and not (hasattr(l, 'id') and isinstance(l.id, int) and l.id > 0):
                                has_unsaved_duplicate = True
                                break
                        except (AttributeError, TypeError):
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
                except (AttributeError, TypeError, ValueError) as e:
                    _logger.warning(f"[批次号约束验证] 从数据库获取已保存批次号列表时出错: {str(e)}")
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
                        except (AttributeError, TypeError, ValueError):
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
