# -*- coding: utf-8 -*-

import logging
from collections import defaultdict
from odoo import http, fields, _
from odoo.http import request
from odoo.osv import expression
from odoo.exceptions import UserError, ValidationError

# 导入原始的控制器类（如果可能）
try:
    from odoo.addons.stock_barcode.controllers.stock_barcode import StockBarcodeController as OriginalStockBarcodeController
    _logger = logging.getLogger(__name__)
    _logger.info("成功导入原始 StockBarcodeController 类")
except ImportError:
    OriginalStockBarcodeController = http.Controller
    _logger = logging.getLogger(__name__)
    _logger.warning("无法导入原始 StockBarcodeController 类，将使用 http.Controller")

_logger = logging.getLogger(__name__)


class StockBarcodeController(OriginalStockBarcodeController):

    @http.route('/stock_barcode/get_specific_barcode_data', type='json', auth='user', csrf=False)
    def get_specific_barcode_data(self, **kwargs):
        """扩展 get_specific_barcode_data 方法，添加日志以跟踪扫码查询
        
        这是扫码时实际调用的方法，用于查询批次号信息
        注意：由于 Odoo 的路由系统，这个路由可能不会覆盖原始路由
        所以我们也在 stock.lot 模型的 _search 方法中添加了日志
        """
        # **关键修复**：使用 print 和 sys.stderr 确保日志输出
        import sys
        print("=" * 80, file=sys.stderr)
        print(f"[扫码查询数据] get_specific_barcode_data 被调用: 条码={kwargs.get('barcode')}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        
        _logger.error(
            f"[扫码查询数据] ========== get_specific_barcode_data 被调用 ========== "
            f"条码={kwargs.get('barcode')}, kwargs={kwargs}"
        )
        
        barcode = kwargs.get('barcode')
        barcodes_by_model = kwargs.get('barcodes_by_model', {})
        domains_by_model = kwargs.get('domains_by_model', {})
        
        _logger.info(
            f"[扫码查询数据] get_specific_barcode_data 被调用: "
            f"条码={barcode}, barcodes_by_model={barcodes_by_model}, "
            f"domains_by_model={domains_by_model}, kwargs keys={list(kwargs.keys())}"
        )
        
        # 如果涉及到批次号查询，添加详细日志
        if barcode or (barcodes_by_model and 'stock.lot' in barcodes_by_model):
            _logger.info(
                f"[扫码查询数据] 查询批次号: 条码={barcode}, "
                f"stock.lot barcodes={barcodes_by_model.get('stock.lot', [])}"
            )
        
        # 调用原始逻辑（复制 stock_barcode 模块的原始实现）
        request.env.context = {**kwargs.get('context', {}), **request.env.context, 'display_default_code': False}
        barcodes_by_model = kwargs.get('barcodes_by_model')
        domains_by_model = kwargs.get('domains_by_model', {})
        universal_domain = domains_by_model.get('all')
        fetch_quant = kwargs.get('fetch_quants')
        nomenclature = request.env.company.nomenclature_id
        result = defaultdict(list)
        product_ids = set()
        
        # **关键修复**：保存原始条码信息，用于后续验证（即使批次号查询结果为空）
        original_barcodes = kwargs.get('barcodes') or [kwargs.get('barcode')] if kwargs.get('barcode') else []
        if not isinstance(original_barcodes, list):
            original_barcodes = [original_barcodes] if original_barcodes else []

        if barcodes_by_model and barcodes_by_model.get('product.product') and not barcodes_by_model.get('stock.lot'):
            barcodes_by_model['stock.lot'] = []

        # If a barcode was given but no model was specified, search for it for all relevant models.
        if not barcodes_by_model:
            barcode_field_by_model = self._get_barcode_field_by_model()
            barcodes = kwargs.get('barcodes') or [kwargs.get('barcode')]
            barcodes_by_model = {model_name: barcodes for model_name in barcode_field_by_model.keys()}
        
        # **关键修复**：如果 barcodes_by_model 中存在 stock.lot 但为空列表，且原始条码存在，则使用原始条码
        # 这样可以确保即使批次号查询结果为空，也能进行验证
        if barcodes_by_model and 'stock.lot' in barcodes_by_model:
            if not barcodes_by_model['stock.lot'] and original_barcodes:
                # 批次号列表为空，但原始条码存在，使用原始条码进行验证
                barcodes_by_model['stock.lot'] = original_barcodes
                _logger.error(
                    f"[扫码验证] 批次号列表为空，使用原始条码进行验证: 原始条码={original_barcodes}"
                )

        for model_name, barcodes in barcodes_by_model.items():
            if not barcodes:
                continue
            barcode_field = request.env[model_name]._barcode_field
            domain = [(barcode_field, 'in', barcodes)]

            if nomenclature.is_gs1_nomenclature:
                # If we use GS1 nomenclature, the domain might need some adjustments.
                converted_barcodes_domain = []
                unconverted_barcodes = []
                for barcode in set(barcodes):
                    try:
                        # If barcode is digits only, cut off the padding to keep the original barcode only.
                        barcode = str(int(barcode))
                        if converted_barcodes_domain:
                            converted_barcodes_domain = expression.OR([
                                converted_barcodes_domain,
                                [(barcode_field, 'ilike', barcode)]
                            ])
                        else:
                            converted_barcodes_domain = [(barcode_field, 'ilike', barcode)]
                    except ValueError:
                        unconverted_barcodes.append(barcode)
                        pass  # Barcode isn't digits only.
                if converted_barcodes_domain:
                    domain = converted_barcodes_domain
                    if unconverted_barcodes:
                        domain = expression.OR([
                            domain,
                            [(barcode_field, 'in', unconverted_barcodes)]
                        ])
            # Adds additionnal domain if applicable.
            domain_for_this_model = domains_by_model.get(model_name)
            if domain_for_this_model:
                domain = expression.AND([domain, domain_for_this_model])
            if universal_domain:
                domain = expression.AND([domain, universal_domain])
            # Search for barcodes' records.
            records = request.env[model_name].search(domain)
            
            # **关键修复**：如果是批次号查询，只在扫码场景下进行验证
            # 关键：必须确保是扫码操作，而不是手动编辑
            # **重要**：即使批次号不存在（records 为空），也需要进行验证
            # 因为用户可能扫描了一个不存在的批次号，但我们需要检查它是否在预填列表中
            if model_name == 'stock.lot':
                # 获取扫描的批次号名称（从查询结果或条码参数中获取）
                if records:
                    lot_names = [r.name for r in records]
                    scanned_barcodes = lot_names
                else:
                    # 批次号不存在，使用扫描的条码作为批次号名称
                    scanned_barcodes = barcodes if isinstance(barcodes, list) else [barcodes] if barcodes else []
                    lot_names = scanned_barcodes
                
                _logger.error(
                    f"[扫码验证] 批次号查询结果: 查询到的批次号记录={[r.name for r in records] if records else []}, "
                    f"扫描的条码={barcodes}, 用于验证的批次号列表={scanned_barcodes}"
                )
                
                # **关键修复**：检查是否真的是扫码操作
                # 只在扫码界面（/barcode-operations/）下才进行验证
                # 如果是从表单编辑界面触发的查询，应该跳过验证
                is_barcode_operation = False
                picking_id = None
                context = kwargs.get('context', {})
                
                # 方法1：检查 referer 是否包含扫码界面路径
                if hasattr(request, 'httprequest'):
                    try:
                        referer = request.httprequest.headers.get('Referer', '')
                        if referer and '/barcode-operations/' in referer:
                            is_barcode_operation = True
                            import re
                            # 尝试从 referer 中提取 picking_id（例如：/barcode-operations/492/）
                            match = re.search(r'/barcode-operations/(\d+)/', referer)
                            if match:
                                picking_id = int(match.group(1))
                                _logger.error(f"[扫码验证] 从 Referer 中提取 picking_id: {picking_id}, referer={referer}")
                    except Exception as e:
                        _logger.debug(f"[扫码验证] 从 Referer 检查时出错: {str(e)}")
                
                # 方法2：检查 path 是否包含扫码界面路径
                if not is_barcode_operation and hasattr(request, 'httprequest'):
                    try:
                        path = request.httprequest.path
                        if '/barcode-operations/' in path:
                            is_barcode_operation = True
                            import re
                            match = re.search(r'/barcode-operations/(\d+)/', path)
                            if match:
                                picking_id = int(match.group(1))
                                _logger.error(f"[扫码验证] 从 Path 中提取 picking_id: {picking_id}, path={path}")
                    except Exception as e:
                        _logger.debug(f"[扫码验证] 从 Path 检查时出错: {str(e)}")
                
                # 方法3：如果确定是扫码操作，尝试从其他方式获取 picking_id
                if is_barcode_operation:
                    # 从 context 中获取
                    if not picking_id:
                        picking_id = (
                            context.get('active_picking_id') or 
                            context.get('default_picking_id') or
                            context.get('picking_id') or
                            context.get('active_id')
                        )
                    
                    # 从 kwargs 中直接获取
                    if not picking_id:
                        picking_id = kwargs.get('picking_id') or kwargs.get('res_id') or kwargs.get('active_id')
                    
                    # 从 request.params 中获取
                    if not picking_id and hasattr(request, 'params'):
                        picking_id = request.params.get('picking_id') or request.params.get('res_id') or request.params.get('active_id')
                    
                    # 通过批次号查询关联的 picking（作为后备方案）
                    if not picking_id and records:
                        try:
                            lot_ids = [r.id for r in records]
                            move_lines = request.env['stock.move.line'].search([
                                ('lot_id', 'in', lot_ids),
                                ('picking_id', '!=', False),
                                ('picking_id.state', 'in', ('draft', 'waiting', 'confirmed', 'assigned', 'partially_available')),
                            ], limit=10, order='write_date desc')
                            
                            if move_lines:
                                picking_ids = list(set([line.picking_id.id for line in move_lines if line.picking_id]))
                                if len(picking_ids) == 1:
                                    picking_id = picking_ids[0]
                                    _logger.error(f"[扫码验证] 通过批次号查询推断 picking_id: {picking_id}")
                        except Exception as e:
                            _logger.debug(f"[扫码验证] 通过批次号查询 picking_id 时出错: {str(e)}")
                
                _logger.error(
                    f"[扫码验证] 是否扫码操作: {is_barcode_operation}, "
                    f"最终获取到的 picking_id: {picking_id}, "
                    f"context keys={list(context.keys()) if context else []}, "
                    f"kwargs keys={list(kwargs.keys())}"
                )
                
                # **关键修复**：检查作业类型是否启用了增强条码验证
                # 只有当作业类型启用了增强条码验证时，才执行增强验证
                enable_enhanced_validation = False
                if is_barcode_operation and picking_id:
                    try:
                        picking = request.env['stock.picking'].browse(picking_id)
                        if picking.exists() and picking.picking_type_id:
                            enable_enhanced_validation = picking.picking_type_id.enable_enhanced_barcode_validation
                            _logger.info(
                                f"[扫码验证] 作业类型配置检查: picking_id={picking_id}, "
                                f"picking_type_id={picking.picking_type_id.id}, "
                                f"enable_enhanced_barcode_validation={enable_enhanced_validation}"
                            )
                    except Exception as e:
                        _logger.warning(f"[扫码验证] 检查作业类型配置时出错: {str(e)}")
                
                # **关键修复**：只在扫码操作且启用增强验证时才进行验证
                # 如果是从表单编辑界面触发的查询，或者未启用增强验证，跳过验证
                if is_barcode_operation and picking_id and enable_enhanced_validation:
                    try:
                        # 重新获取 picking（因为上面已经获取过了）
                        picking = request.env['stock.picking'].browse(picking_id)
                        if picking.exists():
                            # 查询当前 picking 中所有移动（stock.move）
                            moves = picking.move_ids
                            
                            # 收集所有预填的批次号
                            prefilled_lot_names = []
                            saved_lot_names = []
                            # **关键修复**：记录每个批次号对应的记录数量，用于检测重复扫描
                            lot_name_to_lines = {}  # {lot_name: [line1, line2, ...]}
                            
                            for move in moves:
                                # 查询当前移动中所有已保存记录的批次号（预填列表）
                                existing_lines = request.env['stock.move.line'].search([
                                    ('move_id', '=', move.id),
                                    ('lot_name', '!=', False),
                                    ('lot_name', '!=', ''),
                                ])
                                
                                for line in existing_lines:
                                    if line.lot_name:
                                        lot_name_normalized = line.lot_name.strip().lower()
                                        if lot_name_normalized not in prefilled_lot_names:
                                            prefilled_lot_names.append(lot_name_normalized)
                                            saved_lot_names.append(line.lot_name)
                                        # 记录每个批次号对应的记录
                                        if lot_name_normalized not in lot_name_to_lines:
                                            lot_name_to_lines[lot_name_normalized] = []
                                        lot_name_to_lines[lot_name_normalized].append(line)
                            
                            # **关键修复**：从会话变量中获取已扫描但可能还未保存的批次号
                            # 这样可以确保预填列表包含所有已扫描的批次号，无论它们是否已保存到数据库
                            session = request.session
                            scanned_lots_key = f'scanned_lots_{picking_id}'
                            scanned_lots = list(session.get(scanned_lots_key, []) or [])
                            
                            if scanned_lots:
                                _logger.info(
                                    f"[扫码验证] 从会话变量获取已扫描批次号: picking_id={picking_id}, "
                                    f"会话中的批次号={scanned_lots}, 数据库预填列表={saved_lot_names}"
                                )
                                
                                # 将会话变量中的批次号添加到预填列表中
                                # 注意：会话变量中存储的是标准化后的批次号（小写）
                                for scanned_lot_normalized in scanned_lots:
                                    if scanned_lot_normalized and scanned_lot_normalized not in prefilled_lot_names:
                                        # 会话变量中的批次号不在数据库预填列表中，添加到预填列表
                                        prefilled_lot_names.append(scanned_lot_normalized)
                                        # 尝试从数据库中找到原始批次号（保持原始大小写）
                                        # 如果找不到，使用标准化后的批次号
                                        original_lot_name = scanned_lot_normalized
                                        # 查找数据库中是否有此批次号（忽略大小写）
                                        matching_lines = request.env['stock.move.line'].search([
                                            ('move_id', 'in', moves.ids),
                                            ('lot_name', '!=', False),
                                            ('lot_name', '!=', ''),
                                        ])
                                        for line in matching_lines:
                                            if line.lot_name and line.lot_name.strip().lower() == scanned_lot_normalized:
                                                original_lot_name = line.lot_name
                                                break
                                        saved_lot_names.append(original_lot_name)
                                        _logger.info(
                                            f"[扫码验证] 将会话中的批次号添加到预填列表: "
                                            f"标准化批次号={scanned_lot_normalized}, "
                                            f"原始批次号={original_lot_name}"
                                        )
                            
                            _logger.error(
                                f"[扫码验证] 预填批次号列表: {saved_lot_names}, "
                                f"扫描的批次号: {scanned_barcodes}, "
                                f"批次号记录映射: {[(lot, len(lines)) for lot, lines in lot_name_to_lines.items()]}, "
                                f"会话中的批次号={scanned_lots}"
                            )
                            
                            # **关键修复**：验证扫描的批次号
                            # 即使批次号不存在（records 为空），也需要进行验证
                            # 使用 scanned_barcodes 列表进行验证
                            for scanned_barcode in scanned_barcodes:
                                if not scanned_barcode:
                                    continue
                                
                                scanned_lot_name = str(scanned_barcode).strip().lower()
                                
                                # 检查是否在预填列表中
                                if prefilled_lot_names:
                                    if scanned_lot_name not in prefilled_lot_names:
                                        # 批次号不在预填列表中，返回错误
                                        _logger.error(
                                            f"[扫码验证] 批次号不在预填列表中: {scanned_barcode}, "
                                            f"预填列表={saved_lot_names}"
                                        )
                                        raise UserError(
                                            _('批次号不在列表中！\n\n'
                                              '扫描的批次号："%s"\n\n'
                                              '已预填的批次号列表：\n%s\n\n'
                                              '扫码只是验证，批次号必须在预填列表中。\n'
                                              '请先手动预填批次号，然后再扫码验证。\n\n'
                                              '如需添加新批次号，请手动填写，不要使用扫码。')
                                            % (scanned_barcode, '\n'.join(saved_lot_names))
                                        )
                                    
                                    # **关键修复**：检查是否重复扫描
                                    # 重复扫描的定义：
                                    # 1. 如果批次号在预填列表中，且已经有记录存在，说明是重复扫描
                                    # 2. **重要**：扫码只是验证，不应该创建新记录或更新数量
                                    # 3. 用户预填了两卷，说明预填了两个批次号记录，扫码时应该匹配到对应的记录，而不是创建新记录或更新数量
                                    
                                    # 获取该批次号对应的所有记录
                                    lines_for_lot = lot_name_to_lines.get(scanned_lot_name, [])
                                    
                                    # **关键修复**：检查是否重复扫描
                                    # 重复扫描的定义：
                                    # 1. 如果批次号在预填列表中，且已经有记录存在，说明是重复扫描
                                    # 2. **重要**：扫码只是验证，不应该创建新记录或更新数量
                                    # 3. 用户预填了两卷，说明预填了两个批次号记录，扫码时应该匹配到对应的记录，而不是创建新记录或更新数量
                                    
                                    # 查询当前 picking 中所有包含此批次号的移动行（包括所有记录，不管 qty_done 是否 > 0）
                                    # **关键修复**：使用 sudo() 确保获取最新的 qty_done 值
                                    all_lines_with_lot = request.env['stock.move.line'].sudo().search([
                                        ('move_id', 'in', moves.ids),
                                        ('lot_name', '=', scanned_barcode),
                                    ])
                                    
                                    # **关键修复**：清除缓存，确保获取最新的 qty_done 值
                                    if all_lines_with_lot:
                                        # 清除缓存，从数据库重新读取最新值
                                        all_lines_with_lot.invalidate_recordset(['qty_done'])
                                    
                                    # **关键修复**：检查是否重复扫描
                                    # 重复扫描的定义：批次号在预填列表中，且已经有记录存在
                                    # **重要**：如果记录已存在，说明用户已经预填了，扫码只是验证
                                    # 如果用户扫描同一个批次号两次，就应该提示重复扫描
                                    # 
                                    # **关键问题**：由于扫码只是验证，不会修改 qty_done，所以无法通过 qty_done 判断是否已扫描
                                    # 我们需要使用其他方法来检测重复扫描
                                    # 
                                    # **解决方案**：使用会话（session）变量来跟踪已扫描的批次号
                                    # 在同一个会话中，如果批次号已经被扫描过，就提示重复扫描
                                    if all_lines_with_lot:
                                        # **关键修复**：检查是否重复扫描
                                        # 使用 ORM 方式读取 qty_done，避免 SQL 查询导致事务失败
                                        scanned_lot_normalized = scanned_barcode.strip().lower()
                                        
                                        # 使用 ORM 方式读取 qty_done 值
                                        is_duplicate = False
                                        qty_done_map = {}
                                        
                                        # **关键修复**：使用会话变量来跟踪已扫描的批次号
                                        # 因为 qty_done 字段在数据库中可能不存在或不可靠
                                        # 使用会话变量可以在同一会话中立即检测重复扫描
                                        session = request.session
                                        scanned_lots_key = f'scanned_lots_{picking_id}'
                                        scanned_lots = list(session.get(scanned_lots_key, []) or [])
                                        
                                        # **关键修复**：在检查重复之前，先清理会话变量中的重复项
                                        # 确保会话变量中没有重复的批次号
                                        if scanned_lots:
                                            unique_scanned_lots = []
                                            seen = set()
                                            for lot in scanned_lots:
                                                if lot not in seen:
                                                    unique_scanned_lots.append(lot)
                                                    seen.add(lot)
                                            if len(unique_scanned_lots) != len(scanned_lots):
                                                # 有重复项，更新会话变量
                                                session[scanned_lots_key] = unique_scanned_lots
                                                session.modified = True
                                                _logger.error(
                                                    f"[扫码验证] 清理会话变量中的重复项（检查前）: picking_id={picking_id}, "
                                                    f"原始列表={scanned_lots}, 清理后列表={unique_scanned_lots}"
                                                )
                                                scanned_lots = unique_scanned_lots
                                        
                                        scanned_lot_normalized = scanned_barcode.strip().lower()
                                        
                                        _logger.error(
                                            f"[扫码验证] 检查重复扫描: 批次号={scanned_barcode}, "
                                            f"标准化批次号={scanned_lot_normalized}, "
                                            f"picking_id={picking_id}, "
                                            f"会话键={scanned_lots_key}, "
                                            f"会话中已扫描列表={scanned_lots}, "
                                            f"是否在会话中={scanned_lot_normalized in scanned_lots}"
                                        )
                                        
                                        # **关键修复**：优先检查数据库中的实际记录状态
                                        # 如果数据库中不存在记录或记录已被删除（qty_done = 0），即使会话中有，也不应该视为重复
                                        is_duplicate = False
                                        qty_done_map = {}
                                        
                                        # 方法1：检查数据库中的 qty_done（最可靠）
                                        try:
                                            # 尝试使用 ORM 读取 qty_done
                                            all_lines_with_lot.invalidate_recordset(['qty_done'])
                                            read_values = all_lines_with_lot.read(['qty_done'])
                                            qty_done_map = {r['id']: float(r.get('qty_done') or 0.0) for r in read_values}
                                            
                                            # 检查是否有任何记录的 qty_done > 0（包括标记值 0.0001）
                                            duplicate_line_ids = [
                                                lid for lid in all_lines_with_lot.ids
                                                if qty_done_map.get(lid, 0.0) > 0.0
                                            ]
                                            
                                            if duplicate_line_ids:
                                                is_duplicate = True
                                                scanned_qty_dones = [qty_done_map.get(lid, 0.0) for lid in duplicate_line_ids]
                                                _logger.error(
                                                    f"[扫码验证] 重复扫描（数据库qty_done>0）: 批次号={scanned_barcode}, "
                                                    f"已扫描的记录数={len(duplicate_line_ids)}, "
                                                    f"已扫描的记录ID={duplicate_line_ids}, "
                                                    f"qty_done列表={scanned_qty_dones}"
                                                )
                                            else:
                                                _logger.error(
                                                    f"[扫码验证] 检查 qty_done: 批次号={scanned_barcode}, "
                                                    f"记录ID={all_lines_with_lot.ids}, "
                                                    f"qty_done映射={qty_done_map}, 数据库中未检测到重复扫描"
                                                )
                                        except Exception as e:
                                            _logger.warning(f"[扫码验证] 读取 qty_done 失败: {str(e)}, 使用会话跟踪作为备用")
                                            # 如果读取失败，使用会话跟踪作为备用
                                            qty_done_map = {}
                                        
                                        # 方法2：如果数据库中未检测到重复，检查会话变量并同步
                                        # **关键修复**：优先检查数据库状态，如果数据库中没有记录或记录已被删除，从会话中移除
                                        # 这样可以确保会话变量与数据库状态一致
                                        if not is_duplicate:
                                            # **关键修复**：首先同步会话变量与数据库状态
                                            # 如果数据库中不存在记录，说明记录已被删除，从会话中移除
                                            if not all_lines_with_lot:
                                                # 数据库中不存在记录，说明记录已被删除
                                                # 从会话中移除所有该批次号的记录（可能有多条重复的）
                                                if scanned_lot_normalized in scanned_lots:
                                                    # 移除所有重复的批次号（使用列表推导式，移除所有匹配的）
                                                    scanned_lots = [lot for lot in scanned_lots if lot != scanned_lot_normalized]
                                                    session[scanned_lots_key] = scanned_lots
                                                    session.modified = True
                                                    _logger.error(
                                                        f"[扫码验证] 数据库中不存在记录，从会话中移除: 批次号={scanned_barcode}, "
                                                        f"picking_id={picking_id}, 更新后的会话列表={scanned_lots}"
                                                    )
                                                # 允许扫描
                                                is_duplicate = False
                                            elif all_lines_with_lot:
                                                # 数据库中存在记录，检查 qty_done 状态
                                                # 如果所有记录的 qty_done 都是 0，说明记录存在但未被扫描，或者被删除了但记录还在
                                                # 在这种情况下，检查会话变量
                                                all_qty_done_zero = all(
                                                    qty_done_map.get(lid, 0.0) == 0.0 
                                                    for lid in all_lines_with_lot.ids
                                                )
                                                
                                                if all_qty_done_zero:
                                                    # 所有记录的 qty_done 都是 0，说明记录存在但未被扫描
                                                    # 或者记录被删除后重新创建了（qty_done=0）
                                                    # 在这种情况下，如果会话中有该批次号，需要检查是否真的是重复扫描
                                                    # **关键修复**：如果数据库中有记录但 qty_done=0，且会话中有该批次号，
                                                    # 说明可能是删除后重新扫描，应该从会话中移除，允许重新扫描
                                                    if scanned_lot_normalized in scanned_lots:
                                                        # 从会话中移除，允许重新扫描
                                                        scanned_lots = [lot for lot in scanned_lots if lot != scanned_lot_normalized]
                                                        session[scanned_lots_key] = scanned_lots
                                                        session.modified = True
                                                        _logger.error(
                                                            f"[扫码验证] 数据库中记录存在但 qty_done=0，从会话中移除: 批次号={scanned_barcode}, "
                                                            f"记录ID={all_lines_with_lot.ids}, qty_done映射={qty_done_map}, "
                                                            f"picking_id={picking_id}, 更新后的会话列表={scanned_lots}, "
                                                            f"允许重新扫描"
                                                        )
                                                    # 允许扫描
                                                    is_duplicate = False
                                                else:
                                                    # 有记录的 qty_done > 0，说明已经被扫描过
                                                    # 但是在方法1中已经检测到了，所以这里不应该到达
                                                    # 如果到达这里，说明逻辑有问题，记录日志但不视为重复
                                                    _logger.warning(
                                                        f"[扫码验证] 数据库中有记录的 qty_done > 0，但未在方法1中检测到: "
                                                        f"批次号={scanned_barcode}, 记录ID={all_lines_with_lot.ids}, "
                                                        f"qty_done映射={qty_done_map}"
                                                    )
                                                    is_duplicate = False
                                            else:
                                                # 数据库中也没有，会话中也没有，说明是第一次扫描
                                                is_duplicate = False
                                            
                                            # **关键修复**：清理会话变量中的重复项
                                            # 确保会话变量中没有重复的批次号
                                            if scanned_lots:
                                                unique_scanned_lots = []
                                                seen = set()
                                                for lot in scanned_lots:
                                                    if lot not in seen:
                                                        unique_scanned_lots.append(lot)
                                                        seen.add(lot)
                                                if len(unique_scanned_lots) != len(scanned_lots):
                                                    # 有重复项，更新会话变量
                                                    session[scanned_lots_key] = unique_scanned_lots
                                                    session.modified = True
                                                    _logger.error(
                                                        f"[扫码验证] 清理会话变量中的重复项: picking_id={picking_id}, "
                                                        f"原始列表={scanned_lots}, 清理后列表={unique_scanned_lots}"
                                                    )
                                                    scanned_lots = unique_scanned_lots
                                        
                                        if is_duplicate:
                                            # 批次号已经扫描过，提示重复扫描
                                            raise UserError(
                                                _('重复扫描！\n\n'
                                                  '批次号 "%s" 已经扫描过，请勿重复扫描！\n\n'
                                                  '如需继续扫描，请联系系统管理员。')
                                                % scanned_barcode
                                            )
                                        else:
                                            # 批次号还没有扫描过，允许继续
                                            # **关键修复**：立即将会话变量标记为已扫描
                                            # 这样在同一个会话中，后续扫描可以立即检测到重复
                                            if scanned_lot_normalized not in scanned_lots:
                                                scanned_lots.append(scanned_lot_normalized)
                                                session[scanned_lots_key] = scanned_lots.copy()
                                                session.modified = True
                                                _logger.error(
                                                    f"[扫码验证] 添加到会话跟踪: 批次号={scanned_barcode}, "
                                                    f"picking_id={picking_id}, 已扫描列表={scanned_lots}"
                                                )
                                            
                                            # 同时尝试在数据库中标记（如果可能）
                                            line_ids = all_lines_with_lot.ids
                                            if line_ids:
                                                try:
                                                    # 尝试使用 ORM 方式更新 qty_done（如果字段存在）
                                                    # 注意：如果 qty_done 不存在，这个操作可能会失败，但不影响会话跟踪
                                                    all_lines_with_lot.sudo().with_context(
                                                        skip_duplicate_check=True
                                                    ).write({'qty_done': 0.0001})
                                                    all_lines_with_lot.invalidate_recordset(['qty_done'])
                                                    _logger.error(
                                                        f"[扫码验证] 在数据库中标记为正在扫描: 批次号={scanned_barcode}, "
                                                        f"记录ID={line_ids}, 已设置 qty_done=0.0001（标记值）"
                                                    )
                                                except Exception as write_error:
                                                    # 如果更新失败，不影响流程，因为我们已经使用会话跟踪
                                                    _logger.warning(
                                                        f"[扫码验证] 数据库标记失败（不影响流程）: {str(write_error)}"
                                                    )
                                            
                                            _logger.error(
                                                f"[扫码验证] 批次号在预填列表中，且已有记录存在: {scanned_barcode}, "
                                                f"已存在的记录数={len(all_lines_with_lot)}, "
                                                f"已存在的记录ID={[l.id for l in all_lines_with_lot]}, "
                                                f"已添加到会话跟踪，允许继续"
                                            )
                                else:
                                    # 没有预填列表，说明是第一次预填，允许扫描
                                    _logger.error(
                                        f"[扫码验证] 没有预填列表，允许首次扫描: {scanned_barcode}"
                                    )
                    except UserError:
                        # 重新抛出 UserError，让前端显示错误
                        raise
                    except Exception as e:
                        _logger.error(
                            f"[扫码验证] 验证批次号时出错: {str(e)}", 
                            exc_info=True
                        )
                        # 验证出错时不阻止，让后续逻辑处理
                elif is_barcode_operation:
                    # 是扫码操作，但无法获取 picking_id，记录警告
                    _logger.warning(
                        f"[扫码验证] 扫码操作但无法获取 picking_id，跳过验证: 扫描的条码={scanned_barcodes}"
                    )
                else:
                    # 不是扫码操作（可能是手动编辑），跳过验证
                    _logger.info(
                        f"[扫码验证] 非扫码操作（可能是手动编辑），跳过验证: 扫描的条码={scanned_barcodes}"
                    )
            
            # **关键日志**：记录查询到的批次号记录
            if model_name == 'stock.lot' and records:
                _logger.info(
                    f"[扫码查询数据] 查询到批次号记录: 条码={barcode}, "
                    f"批次号记录数={len(records)}, "
                    f"批次号列表={[r.name for r in records]}"
                )
            
            fetched_data = self._get_records_fields_stock_barcode(records)
            if fetch_quant and model_name == 'product.product':
                product_ids = records.ids
            for f_model_name in fetched_data:
                result[f_model_name] = result[f_model_name] + fetched_data[f_model_name]

        if fetch_quant and product_ids:
            quants = request.env['stock.quant'].search([('product_id', 'in', product_ids)])
            fetched_data = self._get_records_fields_stock_barcode(quants)

            for f_model_name in fetched_data:
                result[f_model_name] = result[f_model_name] + fetched_data[f_model_name]
        
        _logger.info(
            f"[扫码查询数据] 查询完成: 返回的数据模型={list(result.keys())}, "
            f"stock.lot 记录数={len(result.get('stock.lot', []))}"
        )
        
        return result

    def _get_records_fields_stock_barcode(self, records):
        """获取记录字段（复制自原始实现）"""
        result = defaultdict(list)
        result[records._name] = records.read(records._get_fields_stock_barcode(), load=False)
        if hasattr(records, '_get_stock_barcode_specific_data'):
            records_data_by_model = records._get_stock_barcode_specific_data()
            for res_model in records_data_by_model:
                result[res_model] += records_data_by_model[res_model]
        return result

    def _get_barcode_field_by_model(self):
        """获取每个模型的条码字段（复制自原始实现）"""
        list_model = [
            'stock.location',
            'product.product',
            'product.packaging',
            'stock.picking',
            'stock.lot',
            'stock.quant.package',
        ]
        return {model: request.env[model]._barcode_field for model in list_model if hasattr(request.env[model], '_barcode_field')}

    @http.route('/stock_barcode/save_barcode_data', type='json', auth='user')
    def save_barcode_data(self, model, res_id, write_field, write_vals, allow_duplicate_scan=False, **kwargs):
        """扩展 save_barcode_data 方法，添加日志以跟踪扫码操作
        
        注意：这个路由会覆盖 stock_barcode 模块的原始路由
        前端调用时：
        - model = 'stock.picking'
        - res_id = picking 的 ID
        - write_field = 'move_line_ids'
        - write_vals = 命令列表，格式如 [[1, line_id, {lot_name: 'xxx', ...}], [0, 0, {lot_name: 'yyy', ...}], ...]
        """
        # **关键修复**：使用 ERROR 级别确保日志输出
        _logger.error(
            f"[扫码保存数据] ========== save_barcode_data 被调用 ========== "
            f"model={model}, res_id={res_id}, write_field={write_field}, "
            f"write_vals类型={type(write_vals)}, write_vals长度={len(write_vals) if isinstance(write_vals, list) else 'N/A'}"
        )
        
        # **关键修复**：处理 stock.picking 的 move_line_ids 写入命令
        if model == 'stock.picking' and write_field == 'move_line_ids' and isinstance(write_vals, list):
            try:
                # 解析命令列表，提取批次号信息
                # 命令格式：[[1, line_id, {lot_name: 'xxx', ...}], [0, 0, {lot_name: 'yyy', ...}], ...]
                # 1 = 更新现有记录, 0 = 创建新记录, 2 = 删除记录, 3 = 取消链接, 4 = 取消删除, 5 = 删除所有
                picking = request.env[model].browse(res_id)
                if not picking.exists():
                    _logger.error(f"[扫码保存数据] picking 不存在: res_id={res_id}")
                else:
                    # **关键修复**：检查作业类型是否启用了增强条码验证
                    # 只有当作业类型启用了增强条码验证时，才执行增强验证
                    enable_enhanced_validation = False
                    if picking.picking_type_id:
                        enable_enhanced_validation = picking.picking_type_id.enable_enhanced_barcode_validation
                        _logger.info(
                            f"[扫码保存数据] 作业类型配置检查: picking_id={res_id}, "
                            f"picking_type_id={picking.picking_type_id.id}, "
                            f"enable_enhanced_barcode_validation={enable_enhanced_validation}"
                        )
                    
                    # 如果没有启用增强验证，跳过所有增强验证逻辑，直接调用父类方法
                    if not enable_enhanced_validation:
                        _logger.info(
                            f"[扫码保存数据] 作业类型未启用增强条码验证，跳过增强验证: picking_id={res_id}"
                        )
                        # 调用父类方法，使用原始的保存逻辑
                        return super(StockBarcodeController, self).save_barcode_data(
                            model, res_id, write_field, write_vals, allow_duplicate_scan, **kwargs
                        )
                    
                    # 获取当前 picking 中所有移动（stock.move）
                    moves = picking.move_ids
                    
                    # 收集所有要创建/更新的批次号
                    scanned_lot_names = {}
                    
                    # **关键修复**：记录 write_vals 的详细内容，用于调试
                    _logger.error(
                        f"[扫码保存数据] write_vals 详细内容: {write_vals}"
                    )
                    
                    for idx, command in enumerate(write_vals):
                        if not isinstance(command, (list, tuple)) or len(command) < 3:
                            continue
                        
                        command_type = command[0]
                        line_vals = command[2] if len(command) > 2 else {}
                        line_id = command[1] if command_type == 1 else None
                        
                        _logger.error(
                            f"[扫码保存数据] 处理命令: 索引={idx}, 类型={command_type}, "
                            f"记录ID={line_id}, line_vals keys={list(line_vals.keys())}, "
                            f"line_vals={line_vals}"
                        )
                        
                        # **关键修复**：不再阻止更新数量
                        # 允许更新 qty_done，这样可以通过 qty_done > 0 来判断是否已扫描
                        
                        # **关键修复**：处理所有更新命令，即使没有 lot_name
                        # 因为保存时可能只包含 qty_done，我们需要从数据库记录中获取 lot_name
                        lot_name = None
                        scanned_lot_name = None
                        
                        # 方法1：从 line_vals 中获取 lot_name（如果有）
                        if 'lot_name' in line_vals and line_vals.get('lot_name'):
                            lot_name = line_vals.get('lot_name')
                            scanned_lot_name = str(lot_name).strip().lower()
                        
                        # 方法2：如果是更新命令，从数据库记录中获取 lot_name
                        elif command_type == 1 and line_id:
                            try:
                                existing_line = request.env['stock.move.line'].browse(line_id)
                                if existing_line.exists() and existing_line.lot_name:
                                    lot_name = existing_line.lot_name
                                    scanned_lot_name = str(lot_name).strip().lower()
                                    _logger.info(
                                        f"[扫码保存数据] 从数据库记录获取批次号: 记录ID={line_id}, "
                                        f"批次号={lot_name}, qty_done={existing_line.qty_done}, "
                                        f"新qty_done={line_vals.get('qty_done', 'N/A')}"
                                    )
                            except Exception as e:
                                _logger.warning(
                                    f"[扫码保存数据] 无法从数据库记录获取批次号: 记录ID={line_id}, 错误={str(e)}"
                                )
                        
                        # **关键修改**：如果启用增强条码验证，且有批次号，按照序列号的方式处理，强制 quantity = 1.0
                        # **重要**：只对非序列号产品应用此逻辑，序列号产品保持原有逻辑
                        if lot_name and scanned_lot_name and command_type in (0, 1) and enable_enhanced_validation:
                            # 检查产品不是序列号追踪（tracking != 'serial'）
                            # 序列号产品应该保持原有的 Odoo 标准逻辑
                            product_tracking = None
                            
                            # 方法1：从 line_vals 中获取 product_id
                            product_id = line_vals.get('product_id')
                            if product_id:
                                try:
                                    product = request.env['product.product'].browse(product_id)
                                    if product.exists():
                                        product_tracking = product.tracking
                                except Exception as e:
                                    _logger.warning(f"[扫码保存数据] 检查产品追踪类型时出错: {str(e)}")
                            
                            # 方法2：如果是更新命令，从现有记录中获取 product_id
                            if product_tracking is None and command_type == 1 and line_id:
                                try:
                                    existing_line = request.env['stock.move.line'].browse(line_id)
                                    if existing_line.exists() and existing_line.product_id:
                                        product_tracking = existing_line.product_id.tracking
                                except Exception as e:
                                    _logger.warning(f"[扫码保存数据] 从数据库记录获取产品追踪类型时出错: {str(e)}")
                            
                            # **关键修复**：只对非序列号产品应用增强验证逻辑
                            # 序列号产品（tracking == 'serial'）保持原有逻辑
                            if product_tracking != 'serial':
                                # **关键修改**：按照序列号的方式，每个批次号对应 1.0 单位
                                # 如果 line_vals 中有 quantity 或 qty_done，确保它们被设置为 1.0
                                if 'quantity' in line_vals:
                                    original_quantity = line_vals.get('quantity')
                                    if original_quantity != 1.0:
                                        _logger.info(
                                            f"[扫码保存数据] 批次号产品数量自动设置为 1.0（启用增强验证）: "
                                            f"批次号={lot_name}, 原数量={original_quantity}, "
                                            f"记录ID={line_id}, 命令类型={command_type}, 追踪类型={product_tracking}"
                                        )
                                        line_vals['quantity'] = 1.0
                                elif 'qty_done' in line_vals:
                                    # **关键修复**：如果只有 qty_done，强制设置 quantity = 1.0 和 qty_done = 1.0
                                    # 按照序列号的方式，每个批次号对应 1.0 单位，不能是其他值
                                    original_qty_done = line_vals.get('qty_done', 0.0)
                                    _logger.info(
                                        f"[扫码保存数据] 批次号产品，强制设置 quantity = 1.0 和 qty_done = 1.0（启用增强验证）: "
                                        f"批次号={lot_name}, 原qty_done={original_qty_done}, "
                                        f"记录ID={line_id}, 命令类型={command_type}, 追踪类型={product_tracking}"
                                    )
                                    # **关键修复**：强制设置 quantity = 1.0 和 qty_done = 1.0
                                    line_vals['quantity'] = 1.0
                                    line_vals['qty_done'] = 1.0
                                    _logger.info(
                                        f"[扫码保存数据] 已强制设置: 批次号={lot_name}, "
                                        f"quantity=1.0, qty_done=1.0, 记录ID={line_id}"
                                    )
                            else:
                                _logger.info(
                                    f"[扫码保存数据] 跳过增强验证（序列号产品保持原有逻辑）: "
                                    f"批次号={lot_name}, 记录ID={line_id}, 追踪类型={product_tracking}"
                                )
                        
                        # **关键修复**：如果扫码时没有设置 lot_quantity 和 lot_unit_name，从产品配置中获取默认值
                        # 这样可以确保入库后附件单位和附件数量正确
                        # **重要**：如果数据库中已有值（用户手动设置），不应该覆盖
                        if lot_name and ('lot_quantity' not in line_vals or not line_vals.get('lot_quantity')) and \
                           ('lot_unit_name' not in line_vals or not line_vals.get('lot_unit_name')):
                            # 获取产品ID
                            product_id = line_vals.get('product_id')
                            existing_lot_quantity = None
                            existing_lot_unit_name = None
                            
                            # **关键修复**：如果是更新命令，先检查数据库中已有的值
                            if command_type == 1 and line_id:
                                try:
                                    existing_line = request.env['stock.move.line'].browse(line_id)
                                    if existing_line.exists():
                                        if existing_line.product_id:
                                            product_id = existing_line.product_id.id
                                        # **关键修复**：如果数据库中已有 lot_quantity 和 lot_unit_name，保留这些值
                                        if existing_line.lot_quantity:
                                            existing_lot_quantity = existing_line.lot_quantity
                                        if existing_line.lot_unit_name:
                                            existing_lot_unit_name = existing_line.lot_unit_name
                                except Exception:
                                    pass
                            
                            # **关键修复**：如果数据库中已有值，使用数据库中的值，不覆盖
                            if existing_lot_quantity is not None:
                                line_vals['lot_quantity'] = existing_lot_quantity
                                _logger.info(
                                    f"[扫码保存数据] 使用数据库中已有的单位数量: 批次号={lot_name}, "
                                    f"记录ID={line_id}, lot_quantity={existing_lot_quantity}"
                                )
                            if existing_lot_unit_name:
                                line_vals['lot_unit_name'] = existing_lot_unit_name
                                if existing_line.lot_unit_name_custom:
                                    line_vals['lot_unit_name_custom'] = existing_line.lot_unit_name_custom
                                _logger.info(
                                    f"[扫码保存数据] 使用数据库中已有的单位名称: 批次号={lot_name}, "
                                    f"记录ID={line_id}, lot_unit_name={existing_lot_unit_name}"
                                )
                            
                            # 只有在数据库中没有值，且 line_vals 中也没有值时，才从产品配置获取
                            if product_id and (existing_lot_quantity is None or existing_lot_unit_name is None):
                                try:
                                    product = request.env['product.product'].browse(product_id)
                                    if product.exists() and product.product_tmpl_id:
                                        product_tmpl = product.product_tmpl_id
                                        
                                        # 从产品配置中获取默认的单位信息
                                        if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                                            # 获取默认单位配置
                                            if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                                                # **关键修复**：只有在 lot_unit_name 为空时才设置
                                                if existing_lot_unit_name is None:
                                                    if product_tmpl.default_unit_config == 'custom':
                                                        # 自定义单位
                                                        if hasattr(product_tmpl, 'quick_unit_name') and product_tmpl.quick_unit_name:
                                                            line_vals['lot_unit_name'] = 'custom'
                                                            line_vals['lot_unit_name_custom'] = product_tmpl.quick_unit_name
                                                        else:
                                                            line_vals['lot_unit_name'] = 'custom'
                                                    else:
                                                        # 标准单位
                                                        line_vals['lot_unit_name'] = product_tmpl.default_unit_config
                                                
                                                # **关键修复**：只有在 lot_quantity 为空时才设置，且只有在产品配置中有值时才设置
                                                if existing_lot_quantity is None:
                                                    # 尝试从多个可能的字段获取单位数量
                                                    lot_quantity_value = None
                                                    
                                                    # 方法1：从 custom_unit_value 获取（旧版）
                                                    if hasattr(product_tmpl, 'custom_unit_value') and product_tmpl.custom_unit_value:
                                                        try:
                                                            lot_quantity_value = float(product_tmpl.custom_unit_value)
                                                        except (ValueError, TypeError):
                                                            pass
                                                    
                                                    # 方法2：从 quick_unit_value 获取（新版）
                                                    if lot_quantity_value is None and hasattr(product_tmpl, 'quick_unit_value') and product_tmpl.quick_unit_value:
                                                        try:
                                                            lot_quantity_value = float(product_tmpl.quick_unit_value)
                                                        except (ValueError, TypeError):
                                                            pass
                                                    
                                                    # 如果产品配置中有值，使用产品配置的值
                                                    if lot_quantity_value is not None:
                                                        line_vals['lot_quantity'] = lot_quantity_value
                                                        _logger.info(
                                                            f"[扫码保存数据] 从产品配置获取单位数量: 批次号={lot_name}, "
                                                            f"产品ID={product_id}, lot_quantity={lot_quantity_value}"
                                                        )
                                                    # **关键修复**：如果产品配置中没有值，不设置默认值 1.0，保留为空
                                                    # 这样用户可以手动填写，或者使用其他方式获取
                                                
                                                _logger.info(
                                                    f"[扫码保存数据] 从产品配置获取单位信息: 批次号={lot_name}, "
                                                    f"产品ID={product_id}, lot_unit_name={line_vals.get('lot_unit_name')}, "
                                                    f"lot_quantity={line_vals.get('lot_quantity', '未设置')}"
                                                )
                                            elif hasattr(product_tmpl, 'custom_unit_name') and product_tmpl.custom_unit_name:
                                                # 旧版配置
                                                # **关键修复**：只有在 lot_unit_name 为空时才设置
                                                if existing_lot_unit_name is None:
                                                    if product_tmpl.custom_unit_name == 'custom':
                                                        if hasattr(product_tmpl, 'custom_unit_name_text') and product_tmpl.custom_unit_name_text:
                                                            line_vals['lot_unit_name'] = 'custom'
                                                            line_vals['lot_unit_name_custom'] = product_tmpl.custom_unit_name_text
                                                        else:
                                                            line_vals['lot_unit_name'] = 'custom'
                                                    else:
                                                        line_vals['lot_unit_name'] = product_tmpl.custom_unit_name
                                                
                                                # **关键修复**：只有在 lot_quantity 为空时才设置，且只有在产品配置中有值时才设置
                                                if existing_lot_quantity is None:
                                                    if hasattr(product_tmpl, 'custom_unit_value') and product_tmpl.custom_unit_value:
                                                        try:
                                                            line_vals['lot_quantity'] = float(product_tmpl.custom_unit_value)
                                                            _logger.info(
                                                                f"[扫码保存数据] 从产品配置获取单位数量（旧版）: 批次号={lot_name}, "
                                                                f"产品ID={product_id}, lot_quantity={line_vals['lot_quantity']}"
                                                            )
                                                        except (ValueError, TypeError):
                                                            # 如果转换失败，不设置默认值
                                                            pass
                                                    # **关键修复**：如果产品配置中没有值，不设置默认值 1.0，保留为空
                                                
                                                _logger.info(
                                                    f"[扫码保存数据] 从产品配置获取单位信息（旧版）: 批次号={lot_name}, "
                                                    f"产品ID={product_id}, lot_unit_name={line_vals.get('lot_unit_name')}, "
                                                    f"lot_quantity={line_vals.get('lot_quantity', '未设置')}"
                                                )
                                except Exception as e:
                                    _logger.warning(
                                        f"[扫码保存数据] 从产品配置获取单位信息失败: 批次号={lot_name}, "
                                        f"产品ID={product_id}, 错误={str(e)}"
                                    )
                        elif lot_name and scanned_lot_name and command_type in (0, 1) and not enable_enhanced_validation:
                            # 如果没有启用增强验证，不强制设置 quantity = 1.0
                            _logger.debug(
                                f"[扫码保存数据] 未启用增强验证，不强制设置 quantity = 1.0: "
                                f"批次号={lot_name}, 记录ID={line_id}"
                            )
                            
                            # **关键修复**：获取 move_id
                            move_id = None
                            
                            # 方法1：从 line_vals 中获取 move_id
                            if 'move_id' in line_vals and line_vals.get('move_id'):
                                move_id = line_vals.get('move_id')
                            
                            # 方法2：如果是更新命令，从现有记录中获取 move_id
                            if not move_id and command_type == 1 and line_id:
                                try:
                                    existing_line = request.env['stock.move.line'].browse(line_id)
                                    if existing_line.exists() and existing_line.move_id:
                                        move_id = existing_line.move_id.id
                                except:
                                    pass
                            
                            # 方法3：从 picking 的 move_ids 中查找（如果 line_vals 包含 product_id）
                            if not move_id and 'product_id' in line_vals:
                                try:
                                    product_id = line_vals.get('product_id')
                                    if product_id:
                                        # 从 picking 的 move_ids 中查找匹配的 move
                                        matching_move = picking.move_ids.filtered(
                                            lambda m: m.product_id.id == product_id
                                        )
                                        if matching_move:
                                            move_id = matching_move[0].id
                                except:
                                    pass
                            
                            # 如果仍然没有 move_id，尝试从 picking 的第一个 move 获取（作为后备）
                            if not move_id and picking.move_ids:
                                move_id = picking.move_ids[0].id
                            
                            if move_id:
                                if move_id not in scanned_lot_names:
                                    scanned_lot_names[move_id] = []
                                scanned_lot_names[move_id].append({
                                    'lot_name': lot_name,
                                    'scanned_lot_name': scanned_lot_name,
                                    'command_type': command_type,
                                    'line_id': line_id,
                                    'product_id': line_vals.get('product_id'),
                                    'command_idx': idx,  # 记录命令索引
                                    'line_vals': line_vals,  # 保存原始的 line_vals
                                })
                    
                    _logger.error(
                        f"[扫码保存数据] 解析命令: 扫描的批次号={[(move_id, [lot['lot_name'] for lot in lots]) for move_id, lots in scanned_lot_names.items()]}"
                    )
                    
                    # 对每个移动，验证批次号
                    for move_id, lot_info_list in scanned_lot_names.items():
                        try:
                            move = request.env['stock.move'].browse(move_id)
                            if not move.exists():
                                continue
                            
                            # 查询当前移动中所有已保存记录的批次号（预填列表）
                            existing_lines = request.env['stock.move.line'].search([
                                ('move_id', '=', move_id),
                                ('lot_name', '!=', False),
                                ('lot_name', '!=', ''),
                            ])
                            existing_lot_names = [
                                line.lot_name.strip().lower() 
                                for line in existing_lines 
                                if line.lot_name
                            ]
                            
                            _logger.error(
                                f"[扫码保存数据] 验证批次号: 移动ID={move_id}, "
                                f"预填列表={existing_lot_names}, "
                                f"扫描的批次号={[lot['lot_name'] for lot in lot_info_list]}"
                            )
                            
                            # 检查当前命令中的重复
                            scanned_in_command = [lot['scanned_lot_name'] for lot in lot_info_list]
                            if len(scanned_in_command) != len(set(scanned_in_command)):
                                # 在当前命令中发现重复
                                duplicates = [lot['lot_name'] for lot in lot_info_list 
                                            if scanned_in_command.count(lot['scanned_lot_name']) > 1]
                                _logger.error(
                                    f"[扫码保存数据] 阻止保存: 在当前操作中发现重复批次号: {list(set(duplicates))}"
                                )
                                raise ValidationError(
                                    _('重复扫描！\n\n'
                                      '批次号 "%s" 在当前操作中重复扫描。\n\n'
                                      '扫码只是验证，每个批次号只能扫描一次。\n'
                                      '请勿重复扫描同一个批次号。')
                                    % list(set(duplicates))[0]
                                )
                            
                            # 检查与已保存记录的重复
                            for lot_info in lot_info_list:
                                scanned_lot_name = lot_info['scanned_lot_name']
                                lot_name = lot_info['lot_name']
                                line_id = lot_info['line_id']
                                command_type = lot_info.get('command_type', 1)  # 从 lot_info 获取 command_type
                                command_idx = lot_info.get('command_idx')
                                line_vals = lot_info.get('line_vals', {})
                                
                                # 查询当前移动中所有包含此批次号的移动行（包括当前记录本身）
                                all_lines_with_lot = request.env['stock.move.line'].search([
                                    ('move_id', '=', move_id),
                                    ('lot_name', '=', lot_name),
                                ])
                                
                                # **关键修复**：检查是否重复扫描
                                # 重复扫描的定义：批次号在预填列表中，且已经有记录存在
                                # **重要**：即使 qty_done=0，只要记录已存在，也应该检测为重复扫描
                                # 
                                # 检测方法：
                                # 1. 检查是否有其他记录（排除当前记录本身）具有相同的批次号
                                # 2. 检查是否在同一个请求中有多个命令试图处理同一个批次号
                                # 3. 检查是否当前命令是更新命令，且批次号已经在预填列表中
                                
                                # 排除当前记录本身（如果是更新命令）
                                existing_lot_names_for_check = [
                                    line.lot_name.strip().lower() 
                                    for line in existing_lines 
                                    if line.lot_name and line.id != line_id
                                ]
                                
                                # **关键修复**：如果批次号在预填列表中，检查是否有其他记录
                                # 或者，如果当前命令是更新命令，且批次号已经在预填列表中，检查是否是重复扫描
                                is_duplicate = False
                                duplicate_reason = ""
                                
                                # 方法1：检查是否有其他记录（排除当前记录本身）具有相同的批次号
                                if scanned_lot_name in existing_lot_names_for_check:
                                    duplicate_lines = [
                                        line for line in existing_lines 
                                        if line.lot_name and 
                                           line.lot_name.strip().lower() == scanned_lot_name and
                                           line.id != line_id
                                    ]
                                    if duplicate_lines:
                                        is_duplicate = True
                                        duplicate_reason = f"批次号已存在于其他记录中（记录数={len(duplicate_lines)}）"
                                
                                # 方法2：检查是否在同一个请求中有多个命令试图处理同一个批次号
                                # 这在上面已经检查过了（scanned_in_command 重复检查）
                                
                                # 方法3：如果当前命令是更新命令，且批次号已经在预填列表中，且记录已存在
                                # **关键修复**：检查当前记录是否已经扫描过（qty_done > 0）
                                if not is_duplicate and command_type == 1 and line_id:
                                    if scanned_lot_name in existing_lot_names:
                                        # 批次号在预填列表中，且当前命令是更新命令
                                        # 检查是否有其他记录具有相同的批次号
                                        other_lines_with_same_lot = [
                                            line for line in all_lines_with_lot 
                                            if line.id != line_id
                                        ]
                                        if other_lines_with_same_lot:
                                            is_duplicate = True
                                            duplicate_reason = f"批次号已存在于其他记录中（记录数={len(other_lines_with_same_lot)}）"
                                        else:
                                            # 没有其他记录，但批次号在预填列表中，且当前命令是更新命令
                                            # **关键修复**：检查当前记录是否已经扫描过（qty_done > 0）
                                            # 现在允许更新 qty_done，所以可以通过 qty_done > 0 来判断是否已扫描
                                            # **重要**：如果原 qty_done > 0，说明已经扫描过，这是重复扫描
                                            # **关键修复**：但是，如果 qty_done 从 > 0 变为 0，这可能是包裹操作，不应该视为重复扫描
                                            existing_line = request.env['stock.move.line'].sudo().browse(line_id)
                                            if existing_line.exists():
                                                # **关键修复**：清除缓存，从数据库重新读取最新的 qty_done 值
                                                existing_line.invalidate_recordset(['qty_done', 'result_package_id', 'package_id'])
                                                # 重新读取记录，获取最新的 qty_done 值
                                                try:
                                                    read_result = existing_line.read(['qty_done', 'result_package_id', 'package_id'])
                                                    if read_result:
                                                        old_qty_done = read_result[0].get('qty_done', 0.0) or 0.0
                                                        old_result_package_id = read_result[0].get('result_package_id')
                                                        old_package_id = read_result[0].get('package_id')
                                                    else:
                                                        old_qty_done = existing_line.qty_done or 0.0
                                                        old_result_package_id = existing_line.result_package_id.id if existing_line.result_package_id else False
                                                        old_package_id = existing_line.package_id.id if existing_line.package_id else False
                                                except Exception as e:
                                                    # 如果读取失败，使用属性访问
                                                    _logger.warning(f"[扫码保存数据] 读取记录失败: {str(e)}, 使用属性访问")
                                                    old_qty_done = existing_line.qty_done or 0.0
                                                    try:
                                                        old_result_package_id = existing_line.result_package_id.id if existing_line.result_package_id else False
                                                        old_package_id = existing_line.package_id.id if existing_line.package_id else False
                                                    except:
                                                        old_result_package_id = False
                                                        old_package_id = False
                                                
                                                new_qty_done = line_vals.get('qty_done')
                                                new_result_package_id = line_vals.get('result_package_id')
                                                new_package_id = line_vals.get('package_id')
                                                
                                                # **关键修复**：检查是否是包裹操作
                                                # 包裹操作的标志：
                                                # 1. qty_done 从 > 0 变为 0（放入包裹或从包裹中移除）- **关键修复**：即使没有创建新记录，也视为包裹操作
                                                # 2. 或者 result_package_id 发生变化（放入/从包裹中移除）
                                                # 3. 或者 package_id 发生变化（从包裹中移除）
                                                # 4. 或者新记录的 quantity = 0（创建新记录但 quantity 为 0，可能是包裹操作）
                                                is_package_operation = False
                                                
                                                # **关键修复**：优先检查 qty_done 从 > 0 变为 0 的情况
                                                # 这是包裹操作的典型特征（放入包裹时，原记录的 qty_done 会被设为 0）
                                                if new_qty_done is not None:
                                                    if old_qty_done > 0 and new_qty_done == 0:
                                                        # qty_done 从 > 0 变为 0，这是包裹操作
                                                        # **关键修复**：即使没有创建新记录，也视为包裹操作
                                                        # 因为放入包裹时，Odoo 会将原记录的 qty_done 设为 0
                                                        is_package_operation = True
                                                        _logger.info(
                                                            f"[扫码保存数据] 检测到包裹操作: 记录ID={line_id}, "
                                                            f"批次号={lot_name}, qty_done 从 {old_qty_done} 变为 0（放入包裹）"
                                                        )
                                                
                                                # 检查 result_package_id 或 package_id 变化
                                                if new_result_package_id is not None and new_result_package_id != old_result_package_id:
                                                    # result_package_id 发生变化，是包裹操作
                                                    is_package_operation = True
                                                    _logger.info(
                                                        f"[扫码保存数据] 检测到包裹操作: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, result_package_id 从 {old_result_package_id} 变为 {new_result_package_id}"
                                                    )
                                                
                                                if new_package_id is not None and new_package_id != old_package_id:
                                                    # package_id 发生变化，是包裹操作
                                                    is_package_operation = True
                                                    _logger.info(
                                                        f"[扫码保存数据] 检测到包裹操作: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, package_id 从 {old_package_id} 变为 {new_package_id}"
                                                    )
                                                
                                                # **关键修复**：检查是否有其他命令创建新记录（quantity = 0 的新记录通常是包裹操作）
                                                if not is_package_operation:
                                                    has_new_record_with_zero_qty = any(
                                                        cmd[0] == 0 and isinstance(cmd[2], dict) and 
                                                        (cmd[2].get('quantity', 1) == 0 or cmd[2].get('qty_done', 1) == 0)
                                                        for cmd in write_vals
                                                        if isinstance(cmd, (list, tuple)) and len(cmd) > 2
                                                    )
                                                    if has_new_record_with_zero_qty:
                                                        is_package_operation = True
                                                        _logger.info(
                                                            f"[扫码保存数据] 检测到包裹操作: 记录ID={line_id}, "
                                                            f"批次号={lot_name}, 有其他命令创建 quantity=0 的新记录（包裹操作）"
                                                        )
                                                
                                                _logger.error(
                                                    f"[扫码保存数据] 检查重复扫描: 记录ID={line_id}, "
                                                    f"批次号={lot_name}, 原qty_done={old_qty_done}, 新qty_done={new_qty_done}, "
                                                    f"是否包裹操作={is_package_operation}, line_vals keys={list(line_vals.keys())}"
                                                )
                                                
                                                # **关键修复**：如果是包裹操作，不应该视为重复扫描
                                                # 包裹操作时，从会话变量中移除该批次号（因为 qty_done 被设为 0，不再是"已扫描"状态）
                                                # **关键修复**：同时设置扫描顺序，用于保持包裹中记录的顺序
                                                if is_package_operation:
                                                    # 包裹操作，允许继续
                                                    _logger.info(
                                                        f"[扫码保存数据] 包裹操作，允许继续: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, qty_done={old_qty_done} -> {new_qty_done}"
                                                    )
                                                    # **关键修复**：包裹操作时，从会话变量中移除该批次号
                                                    # 因为 qty_done 被设为 0，说明该批次号不再处于"已扫描"状态
                                                    # **关键修复**：同时设置扫描顺序，用于保持包裹中记录的顺序
                                                    try:
                                                        session = request.session
                                                        scanned_lots_key = f'scanned_lots_{res_id}'
                                                        scanned_lots = list(session.get(scanned_lots_key, []) or [])
                                                        scanned_lot_normalized = str(lot_name).strip().lower()
                                                        
                                                        # **关键修复**：设置扫描顺序
                                                        # 查找该批次号在扫描顺序中的位置
                                                        if scanned_lot_normalized in scanned_lots:
                                                            scan_index = scanned_lots.index(scanned_lot_normalized)
                                                            # 设置扫描顺序（从1开始，不是从0开始）
                                                            line_vals['scan_sequence'] = scan_index + 1
                                                            _logger.info(
                                                                f"[扫码保存数据] 包裹操作，设置扫描顺序: 记录ID={line_id}, "
                                                                f"批次号={lot_name}, 扫描顺序={scan_index + 1}"
                                                            )
                                                        
                                                        if scanned_lot_normalized in scanned_lots:
                                                            scanned_lots.remove(scanned_lot_normalized)
                                                            session[scanned_lots_key] = scanned_lots
                                                            session.modified = True
                                                            _logger.info(
                                                                f"[扫码保存数据] 包裹操作，从会话中移除批次号: 批次号={lot_name}, "
                                                                f"picking_id={res_id}, 更新后的会话列表={scanned_lots}"
                                                            )
                                                    except Exception as e:
                                                        _logger.warning(
                                                            f"[扫码保存数据] 包裹操作时处理扫描顺序失败: {str(e)}"
                                                        )
                                                # **关键修复**：如果原 qty_done > 0，且新 qty_done 不是 0，说明已经扫描过，这是重复扫描
                                                # 注意：如果 new_qty_done 是 None（没有更新 qty_done），且 old_qty_done > 0，也应该检查其他条件
                                                elif old_qty_done > 0 and (new_qty_done is None or new_qty_done > 0):
                                                    # 记录已经扫描过（qty_done > 0），且新 qty_done 不是 0，这是重复扫描
                                                    is_duplicate = True
                                                    duplicate_reason = f"记录已经扫描过（原qty_done={old_qty_done}，新qty_done={new_qty_done if new_qty_done is not None else '未更新'}）"
                                                    _logger.error(
                                                        f"[扫码保存数据] 检测到重复扫描: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, 原qty_done={old_qty_done}, 新qty_done={new_qty_done}"
                                                    )
                                                elif new_qty_done is not None and new_qty_done > old_qty_done and old_qty_done == 0:
                                                    # 第一次扫描，从 0 更新到 > 0，允许
                                                    _logger.info(
                                                        f"[扫码保存数据] 第一次扫描: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, 从 qty_done=0 更新到 qty_done={new_qty_done}"
                                                    )
                                                elif new_qty_done is not None and new_qty_done == old_qty_done and old_qty_done > 0:
                                                    # 重复扫描，qty_done 没有变化但已经 > 0
                                                    is_duplicate = True
                                                    duplicate_reason = f"记录已经扫描过（qty_done={old_qty_done}，重复扫描时 qty_done 未变化）"
                                                    _logger.error(
                                                        f"[扫码保存数据] 检测到重复扫描: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, qty_done={old_qty_done}，重复扫描"
                                                    )
                                                else:
                                                    # 记录还没有扫描过，这是第一次扫描，允许
                                                    _logger.info(
                                                        f"[扫码保存数据] 其他情况: 记录ID={line_id}, "
                                                        f"批次号={lot_name}, 原qty_done={old_qty_done}, 新qty_done={new_qty_done}"
                                                    )
                                
                                if is_duplicate:
                                    # 检测到重复扫描，提示用户
                                    _logger.error(
                                        f"[扫码保存数据] 检测到重复扫描: 批次号 {lot_name}, "
                                        f"移动ID={move_id}, 记录ID={line_id}, "
                                        f"原因={duplicate_reason}"
                                    )
                                    
                                    # 检查是否允许重复扫描
                                    if not allow_duplicate_scan:
                                        # 没有确认，返回需要确认的错误
                                        raise UserError(
                                            _('重复扫描！\n\n'
                                              '批次号 "%s" 已经扫描过，请勿重复扫描！\n\n'
                                              '扫码只是验证，不会修改库存移动的数量。\n\n'
                                              '如需继续扫描，请联系系统管理员。')
                                            % lot_name
                                        )
                                    else:
                                        # 用户已确认，允许继续，只记录警告
                                        _logger.info(
                                            f"[扫码保存数据] 用户确认继续重复扫描: 批次号 {lot_name}"
                                        )
                                
                                # 检查是否在预填列表中
                                if existing_lines:
                                    # 有预填列表，检查批次号是否在列表中
                                    if scanned_lot_name not in existing_lot_names:
                                        # 批次号不在预填列表中
                                        unique_lot_names = list(set([
                                            line.lot_name 
                                            for line in existing_lines 
                                            if line.lot_name and line.lot_name.strip()
                                        ]))
                                        _logger.error(
                                            f"[扫码保存数据] 阻止保存: 批次号 {lot_name} 不在预填列表中, "
                                            f"移动ID={move_id}, 预填列表={unique_lot_names}"
                                        )
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
                                        # **关键修复**：批次号在预填列表中，允许更新
                                        # 不再阻止更新数量，允许更新 qty_done
                                        # **注意**：不在这里更新会话变量，只有在保存成功后才更新
                                        _logger.info(
                                            f"[扫码保存数据] 批次号在预填列表中: 批次号 {lot_name}, "
                                            f"移动ID={move_id}, 允许更新（保存成功后将添加到会话跟踪）"
                                        )
                        except ValidationError:
                            raise
                        except UserError:
                            # 重新抛出 UserError，让前端显示错误
                            raise
                        except Exception as e:
                            _logger.error(
                                f"[扫码保存数据] 验证批次号时出错: 移动ID={move_id}, 错误={str(e)}", 
                                exc_info=True
                            )
                            # 验证出错时不阻止，让模型方法处理
                    
                    # **关键修复**：不再移除命令，允许更新数量
                    # 现在允许更新 qty_done，所以不需要移除命令
            except ValidationError:
                raise
            except UserError:
                # 重新抛出 UserError，让前端显示错误
                raise
            except Exception as e:
                _logger.error(
                    f"[扫码保存数据] 解析命令时出错: {str(e)}", 
                    exc_info=True
                )
                # 解析出错时不阻止，让原始逻辑处理
        
        # **关键修复**：在保存之前处理删除命令，从会话中移除批次号
        # 这样可以在记录被删除之前读取批次号
        deleted_lots_before_save = []
        if res_id and model == 'stock.picking' and isinstance(write_vals, list):
            try:
                # 在保存之前，检测删除命令并读取批次号
                for idx, command in enumerate(write_vals):
                    if not isinstance(command, (list, tuple)) or len(command) < 2:
                        continue
                    
                    command_type = command[0]
                    line_id = command[1] if len(command) > 1 else None
                    
                    # **关键修复**：检测删除命令（command_type == 2 表示删除）
                    if command_type == 2 and line_id:
                        # 删除命令：在删除前读取批次号
                        try:
                            deleted_line = request.env['stock.move.line'].sudo().browse(line_id)
                            if deleted_line.exists():
                                # 在删除前读取批次号
                                deleted_line.invalidate_recordset(['lot_name'])
                                read_result = deleted_line.read(['lot_name'])
                                if read_result and read_result[0].get('lot_name'):
                                    deleted_lot_name = read_result[0].get('lot_name')
                                    deleted_lot_normalized = str(deleted_lot_name).strip().lower()
                                    if deleted_lot_normalized not in deleted_lots_before_save:
                                        deleted_lots_before_save.append(deleted_lot_normalized)
                                        _logger.error(
                                            f"[扫码保存数据] 保存前检测到删除命令: 批次号={deleted_lot_name}, "
                                            f"记录ID={line_id}, 命令索引={idx}"
                                        )
                        except Exception as e:
                            _logger.warning(
                                f"[扫码保存数据] 读取删除记录的批次号失败: 记录ID={line_id}, 错误={str(e)}"
                            )
                
                # 在保存之前，从会话中移除被删除的批次号
                if deleted_lots_before_save:
                    session = request.session
                    scanned_lots_key = f'scanned_lots_{res_id}'
                    scanned_lots = list(session.get(scanned_lots_key, []) or [])
                    
                    # **关键修复**：移除所有被删除的批次号（包括重复的）
                    # 使用列表推导式，移除所有匹配的批次号，而不是只移除第一个
                    original_scanned_lots = scanned_lots.copy()
                    for deleted_lot_normalized in deleted_lots_before_save:
                        # 移除所有匹配的批次号（可能有多条重复的）
                        scanned_lots = [lot for lot in scanned_lots if lot != deleted_lot_normalized]
                    
                    # 如果列表发生了变化，更新会话变量
                    if scanned_lots != original_scanned_lots:
                        session[scanned_lots_key] = scanned_lots
                        session.modified = True
                        removed_lots = set(original_scanned_lots) - set(scanned_lots)
                        _logger.error(
                            f"[扫码保存数据] 保存前从会话中移除被删除的批次号: 移除的批次号={list(removed_lots)}, "
                            f"picking_id={res_id}, 原始列表={original_scanned_lots}, 更新后的会话列表={scanned_lots}"
                        )
                    
                    # **关键修复**：清理会话变量中的重复项
                    # 确保会话变量中没有重复的批次号
                    if scanned_lots:
                        unique_scanned_lots = []
                        seen = set()
                        for lot in scanned_lots:
                            if lot not in seen:
                                unique_scanned_lots.append(lot)
                                seen.add(lot)
                        if len(unique_scanned_lots) != len(scanned_lots):
                            # 有重复项，更新会话变量
                            session[scanned_lots_key] = unique_scanned_lots
                            session.modified = True
                            _logger.error(
                                f"[扫码保存数据] 保存前清理会话变量中的重复项: picking_id={res_id}, "
                                f"原始列表={scanned_lots}, 清理后列表={unique_scanned_lots}"
                            )
                    
                    _logger.error(
                        f"[扫码保存数据] 保存前会话变量已更新: picking_id={res_id}, "
                        f"最终会话中的批次号={session.get(scanned_lots_key, [])}"
                    )
            except Exception as e:
                _logger.warning(
                    f"[扫码保存数据] 保存前处理删除命令失败: {str(e)}", 
                    exc_info=True
                )
        
        # **关键修复**：为扫码操作添加上下文标识
        # 在调用 write 之前，设置 context 以便模型方法能够识别这是扫码操作
        context = dict(request.env.context)
        context.update({
            'barcode_view': True,
            'from_barcode': True,
            'list_view_ref': 'stock.view_stock_move_line_operation_tree',
            'form_view_ref': 'stock.view_move_line_mobile_form',
        })
        
        # 调用原始逻辑（完全复制 stock_barcode 模块的原始实现）
        try:
            if not res_id:
                # 创建新记录
                result = request.env[model].with_context(**context).barcode_write(write_vals)
                _logger.error(
                    f"[扫码保存数据] barcode_write 调用完成: 模型={model}, "
                    f"结果={result if isinstance(result, (int, str)) else '记录已创建'}"
                )
            else:
                # 更新现有记录
                target_record = request.env[model].with_context(**context).browse(res_id)
                _logger.error(
                    f"[扫码保存数据] 准备调用 write 方法: 模型={model}, 记录ID={res_id}, "
                    f"字段={write_field}, write_vals长度={len(write_vals) if isinstance(write_vals, list) else 'N/A'}, context={context}"
                )
                
                # **关键修复**：如果 write_vals 为空或只有删除命令，不执行保存
                if isinstance(write_vals, list) and not write_vals:
                    _logger.error(
                        f"[扫码保存数据] write_vals 为空，不执行保存操作，返回原始数据"
                    )
                    result = target_record._get_stock_barcode_data()
                else:
                    target_record.write({write_field: write_vals})
                    result = target_record._get_stock_barcode_data()
                    _logger.error(
                        f"[扫码保存数据] write 调用完成: 模型={model}, 记录ID={res_id}, "
                        f"字段={write_field}, 结果记录数={len(result) if isinstance(result, list) else 1}"
                    )
            
            # **关键修复**：保存成功后，更新会话变量，标记批次号已扫描
            # 只有在保存成功后才添加到会话列表
            if res_id and model == 'stock.picking':
                try:
                    _logger.error(
                        f"[扫码保存数据] 保存成功后，开始提取批次号: res_id={res_id}, "
                        f"write_vals类型={type(write_vals)}, write_vals长度={len(write_vals) if isinstance(write_vals, list) else 'N/A'}"
                    )
                    
                    # **关键修复**：提取新增/更新的批次号，添加到会话中
                    scanned_lots_in_request = []
                    
                    if isinstance(write_vals, list):
                        _logger.error(
                            f"[扫码保存数据] write_vals 是列表，开始遍历命令: 命令数量={len(write_vals)}"
                        )
                        for idx, command in enumerate(write_vals):
                            if not isinstance(command, (list, tuple)) or len(command) < 2:
                                _logger.warning(
                                    f"[扫码保存数据] 跳过无效命令: 索引={idx}, 命令={command}"
                                )
                                continue
                            
                            command_type = command[0]
                            line_id = command[1] if len(command) > 1 else None
                            line_vals = command[2] if len(command) > 2 else {}
                            
                            # 跳过删除命令（已经在保存前处理）
                            if command_type == 2:
                                continue
                            
                            # 获取批次号（用于新增/更新命令）
                            lot_name = None
                            
                            # 方法1：从 line_vals 中获取批次号
                            if isinstance(line_vals, dict) and 'lot_name' in line_vals and line_vals.get('lot_name'):
                                lot_name = line_vals.get('lot_name')
                                _logger.error(
                                    f"[扫码保存数据] 从 line_vals 获取批次号: 批次号={lot_name}, 命令索引={idx}"
                                )
                            
                            # 方法2：如果是更新命令（command_type == 1），从数据库记录中获取批次号
                            # **关键修复**：即使 line_vals 中没有 lot_name，也要从数据库读取
                            if not lot_name and command_type == 1 and line_id:
                                try:
                                    # 重新查询数据库，获取最新的批次号
                                    existing_line = request.env['stock.move.line'].sudo().browse(line_id)
                                    if existing_line.exists():
                                        # 清除缓存，重新读取
                                        existing_line.invalidate_recordset(['lot_name', 'qty_done', 'result_package_id', 'package_id'])
                                        read_result = existing_line.read(['lot_name', 'qty_done', 'result_package_id', 'package_id'])
                                        if read_result and read_result[0].get('lot_name'):
                                            lot_name = read_result[0].get('lot_name')
                                            qty_done_after_save = read_result[0].get('qty_done', 0.0) or 0.0
                                            new_qty_done_from_line_vals = line_vals.get('qty_done')
                                            
                                            # **关键修复**：检查是否是包裹操作
                                            # 如果 qty_done 被设为 0，这是包裹操作，不应该添加到会话变量
                                            is_package_op = False
                                            if new_qty_done_from_line_vals is not None:
                                                # 检查保存后的 qty_done 是否为 0
                                                if new_qty_done_from_line_vals == 0 or qty_done_after_save == 0:
                                                    # 检查原 qty_done 是否 > 0（从 > 0 变为 0 是包裹操作）
                                                    try:
                                                        # 需要从保存前的状态判断，但由于已经保存，我们使用 line_vals 中的新值
                                                        # 如果 line_vals 中明确指定 qty_done=0，且之前 qty_done > 0，这是包裹操作
                                                        # 但由于我们已经保存，无法获取保存前的值
                                                        # 所以，如果保存后 qty_done=0，我们检查是否有包裹相关的字段变化
                                                        result_package_id_after = read_result[0].get('result_package_id')
                                                        package_id_after = read_result[0].get('package_id')
                                                        new_result_package_id = line_vals.get('result_package_id')
                                                        new_package_id = line_vals.get('package_id')
                                                        
                                                        # 如果包裹相关字段发生变化，或者是 qty_done 被设为 0，视为包裹操作
                                                        if (new_result_package_id is not None and new_result_package_id != result_package_id_after) or \
                                                           (new_package_id is not None and new_package_id != package_id_after) or \
                                                           (new_qty_done_from_line_vals == 0):
                                                            is_package_op = True
                                                    except:
                                                        pass
                                            
                                            _logger.error(
                                                f"[扫码保存数据] 从数据库重新读取批次号: 批次号={lot_name}, "
                                                f"记录ID={line_id}, qty_done={qty_done_after_save}, "
                                                f"是否包裹操作={is_package_op}, 命令索引={idx}"
                                            )
                                            
                                            # **关键修复**：如果是包裹操作（qty_done=0），不应该添加到会话变量
                                            if is_package_op:
                                                _logger.info(
                                                    f"[扫码保存数据] 跳过添加到会话变量（包裹操作）: 批次号={lot_name}, "
                                                    f"记录ID={line_id}, qty_done={qty_done_after_save}"
                                                )
                                                continue  # 跳过，不添加到会话变量
                                except Exception as e:
                                    _logger.warning(
                                        f"[扫码保存数据] 从数据库重新读取批次号失败: 记录ID={line_id}, 错误={str(e)}"
                                    )
                            
                            # 方法3：如果是创建命令（command_type == 0），从 line_vals 中获取批次号
                            if not lot_name and command_type == 0 and isinstance(line_vals, dict) and 'lot_name' in line_vals:
                                lot_name = line_vals.get('lot_name')
                            
                            # **关键修复**：检查创建的新记录是否是包裹操作
                            # 如果新记录的 quantity = 0 或 qty_done = 0，这可能是包裹操作，不应该添加到会话变量
                            if lot_name and command_type == 0:
                                new_qty = line_vals.get('quantity', 1)
                                new_qty_done = line_vals.get('qty_done', 1)
                                if new_qty == 0 or new_qty_done == 0:
                                    # 新记录的 quantity = 0，这可能是包裹操作
                                    _logger.info(
                                        f"[扫码保存数据] 跳过添加到会话变量（包裹操作，新记录 quantity=0）: 批次号={lot_name}, "
                                        f"quantity={new_qty}, qty_done={new_qty_done}"
                                    )
                                    continue  # 跳过，不添加到会话变量
                            
                            if lot_name:
                                scanned_lot_normalized = str(lot_name).strip().lower()
                                if scanned_lot_normalized not in scanned_lots_in_request:
                                    scanned_lots_in_request.append(scanned_lot_normalized)
                                    _logger.error(
                                        f"[扫码保存数据] 添加到批次号列表: 批次号={scanned_lot_normalized}, "
                                        f"原始批次号={lot_name}, 当前列表={scanned_lots_in_request}"
                                    )
                    
                    _logger.error(
                        f"[扫码保存数据] 提取到的批次号列表: {scanned_lots_in_request}, "
                        f"res_id={res_id}, model={model}"
                    )
                    
                    # **关键修复**：更新会话变量，添加新增/更新的批次号
                    # **关键修复**：同时处理包裹操作，确保所有被放入包裹的记录都按照扫描顺序设置 scan_sequence
                    if scanned_lots_in_request:
                        session = request.session
                        scanned_lots_key = f'scanned_lots_{res_id}'
                        scanned_lots = list(session.get(scanned_lots_key, []) or [])
                        
                        _logger.error(
                            f"[扫码保存数据] 更新会话变量: picking_id={res_id}, "
                            f"新增/更新的批次号={scanned_lots_in_request}, 当前会话中的批次号={scanned_lots}"
                        )
                        
                        # **关键修复**：先同步会话变量与数据库状态，移除数据库中不存在的批次号
                        # **关键修复**：同时移除 qty_done=0 的批次号（这些是包裹操作，不应该在会话中）
                        # 这样可以确保会话变量与数据库状态一致
                        # **关键修复**：但是，放入包裹时，qty_done 会被设为 0，所以我们需要在放入包裹时保存扫描顺序
                        try:
                            # 查询所有有批次号的移动行
                            picking = request.env['stock.picking'].browse(res_id)
                            if picking.exists():
                                all_lines_with_lot = request.env['stock.move.line'].search([
                                    ('picking_id', '=', res_id),
                                    ('lot_name', '!=', False),
                                    ('lot_name', '!=', ''),
                                ])
                                
                                # **关键修复**：处理包裹操作，确保所有被放入包裹的记录都按照扫描顺序设置 scan_sequence
                                # 放入包裹时，可能多个记录同时被放入，我们需要按照扫描顺序设置 scan_sequence
                                # 首先，找出所有被放入包裹的记录（result_package_id 不为 False 的记录）
                                package_lines = all_lines_with_lot.filtered(lambda l: l.result_package_id)
                                
                                if package_lines:
                                    # 有记录被放入包裹，按照扫描顺序设置 scan_sequence
                                    for line in package_lines:
                                        if line.lot_name:
                                            lot_name_normalized = line.lot_name.strip().lower()
                                            # 查找该批次号在扫描顺序中的位置
                                            # **关键修复**：使用完整的 scanned_lots 列表（包括新添加的批次号）
                                            full_scanned_lots = scanned_lots + scanned_lots_in_request
                                            # 去重，保持顺序
                                            seen_lots = set()
                                            unique_full_scanned_lots = []
                                            for lot in full_scanned_lots:
                                                if lot not in seen_lots:
                                                    unique_full_scanned_lots.append(lot)
                                                    seen_lots.add(lot)
                                            
                                            try:
                                                scan_index = unique_full_scanned_lots.index(lot_name_normalized)
                                                # 设置扫描顺序（从1开始，不是从0开始）
                                                try:
                                                    line.with_context(skip_quantity_fix=True).write({'scan_sequence': scan_index + 1})
                                                    _logger.info(
                                                        f"[扫码保存数据] 包裹操作，设置扫描顺序: 记录ID={line.id}, "
                                                        f"批次号={line.lot_name}, 扫描顺序={scan_index + 1}"
                                                    )
                                                except Exception as e:
                                                    _logger.warning(
                                                        f"[扫码保存数据] 包裹操作，设置扫描顺序失败: 记录ID={line.id}, "
                                                        f"批次号={line.lot_name}, 错误={str(e)}"
                                                    )
                                            except ValueError:
                                                # 批次号不在扫描列表中，保持原有顺序
                                                _logger.debug(
                                                    f"[扫码保存数据] 包裹操作，批次号不在扫描列表中: 记录ID={line.id}, "
                                                    f"批次号={line.lot_name}"
                                                )
                                
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
                                    removed_lots = set(original_scanned_lots) - set(scanned_lots)
                                    _logger.error(
                                        f"[扫码保存数据] 同步会话变量: picking_id={res_id}, "
                                        f"从会话中移除的批次号={list(removed_lots)} "
                                        f"（数据库中不存在或 qty_done=0）, "
                                        f"原始列表={original_scanned_lots}, 同步后列表={scanned_lots}"
                                    )
                        except Exception as e:
                            _logger.warning(f"[扫码保存数据] 同步会话变量时出错: {str(e)}")
                        
                        # 添加新增/更新的批次号
                        for scanned_lot_normalized in scanned_lots_in_request:
                            if scanned_lot_normalized not in scanned_lots:
                                scanned_lots.append(scanned_lot_normalized)
                                _logger.error(
                                    f"[扫码保存数据] 保存成功后，添加到会话跟踪: 批次号={scanned_lot_normalized}, "
                                    f"picking_id={res_id}, 已扫描列表={scanned_lots}"
                                )
                        
                        # **关键修复**：清理会话变量中的重复项
                        # 确保会话变量中没有重复的批次号
                        if scanned_lots:
                            unique_scanned_lots = []
                            seen = set()
                            for lot in scanned_lots:
                                if lot not in seen:
                                    unique_scanned_lots.append(lot)
                                    seen.add(lot)
                            if len(unique_scanned_lots) != len(scanned_lots):
                                # 有重复项，更新会话变量
                                _logger.error(
                                    f"[扫码保存数据] 清理会话变量中的重复项: picking_id={res_id}, "
                                    f"原始列表={scanned_lots}, 清理后列表={unique_scanned_lots}"
                                )
                                scanned_lots = unique_scanned_lots
                        
                        session[scanned_lots_key] = scanned_lots
                        session.modified = True
                        _logger.error(
                            f"[扫码保存数据] 会话变量已更新: picking_id={res_id}, "
                            f"最终会话中的批次号={session.get(scanned_lots_key, [])}"
                        )
                except Exception as e:
                    _logger.error(
                        f"[扫码保存数据] 更新会话变量失败: {str(e)}", 
                        exc_info=True
                    )
            
            # **关键修复**：在保存后，处理包裹操作，确保所有被放入包裹的记录都按照扫描顺序设置 scan_sequence
            # 这是为了确保在保存后，所有被放入包裹的记录都按照扫描顺序设置 scan_sequence
            # **关键**：必须在保存后处理，因为只有在保存后，result_package_id 才会被正确设置
            try:
                if model == 'stock.picking' and res_id:
                    _logger.info(
                        f"[扫码保存数据] 开始处理保存后的包裹操作: model={model}, res_id={res_id}"
                    )
                    picking = request.env['stock.picking'].browse(res_id)
                    if picking.exists():
                        # **关键修复**：查询所有有批次号且已放入包裹的记录
                        # 按照 scan_sequence 排序，确保顺序正确
                        # **关键**：由于模型的 _order 已设置为 'scan_sequence, id'，所以查询结果会自动按照扫描顺序排序
                        package_lines = request.env['stock.move.line'].search([
                            ('picking_id', '=', res_id),
                            ('lot_name', '!=', False),
                            ('lot_name', '!=', ''),
                            ('result_package_id', '!=', False),
                        ], order='scan_sequence, id')  # 显式指定排序，确保按照扫描顺序排序
                        
                        _logger.info(
                            f"[扫码保存数据] 保存后查询包裹记录: picking_id={res_id}, "
                            f"包裹记录数={len(package_lines)}, 记录ID={[l.id for l in package_lines]}, "
                            f"包裹ID={[l.result_package_id.id if l.result_package_id else False for l in package_lines]}"
                        )
                        
                        if package_lines:
                            session = request.session
                            scanned_lots_key = f'scanned_lots_{res_id}'
                            scanned_lots = list(session.get(scanned_lots_key, []) or [])
                            
                            # **关键修复**：按包裹分组，统计每个包裹中的记录数
                            # 这样可以更好地支持多个包裹的情况
                            package_groups = {}
                            for line in package_lines:
                                package_id = line.result_package_id.id if line.result_package_id else False
                                if package_id not in package_groups:
                                    package_groups[package_id] = []
                                package_groups[package_id].append(line)
                            
                            _logger.info(
                                f"[扫码保存数据] 保存后处理包裹操作: picking_id={res_id}, "
                                f"会话中的批次号={scanned_lots}, 包裹记录数={len(package_lines)}, "
                                f"包裹数量={len(package_groups)}, 包裹ID={list(package_groups.keys())}"
                            )
                            
                            # **关键修复**：如果会话变量中没有扫描顺序，从数据库记录中恢复
                            # 这样可以确保即使会话变量丢失，也能保持扫描顺序
                            # **关键**：按照记录的创建时间或 ID 顺序，重建扫描顺序
                            if not scanned_lots:
                                # 从数据库记录中恢复扫描顺序
                                # 按照记录的创建时间或 ID 顺序，重建扫描顺序
                                scanned_lots = []
                                # 按照记录的创建时间排序，获取扫描顺序
                                sorted_lines = package_lines.sorted(lambda l: (l.create_date or fields.Datetime.now(), l.id))
                                for line in sorted_lines:
                                    if line.lot_name:
                                        lot_name_normalized = line.lot_name.strip().lower()
                                        if lot_name_normalized not in scanned_lots:
                                            scanned_lots.append(lot_name_normalized)
                                
                                # 更新会话变量
                                session[scanned_lots_key] = scanned_lots
                                session.modified = True
                                _logger.info(
                                    f"[扫码保存数据] 从数据库记录恢复扫描顺序: picking_id={res_id}, "
                                    f"扫描顺序={scanned_lots}"
                                )
                            
                            # **关键修复**：按照扫描顺序，为所有被放入包裹的记录设置 scan_sequence
                            # 这样可以确保所有包裹（无论有多少个）中的记录都按照扫描顺序排列
                            # **关键修复**：支持多个包裹（三个、四个、五个...），每个包裹中的记录都按照扫描顺序设置
                            if scanned_lots:
                                _logger.info(
                                    f"[扫码保存数据] 开始为包裹记录设置扫描顺序: picking_id={res_id}, "
                                    f"会话中的批次号={scanned_lots}, 包裹记录数={len(package_lines)}, "
                                    f"包裹数量={len(package_groups)}"
                                )
                                # **关键修复**：为每个包裹中的每个记录设置扫描顺序
                                # 无论有多少个包裹，都会正确处理每个包裹中的记录
                                for package_id, lines in package_groups.items():
                                    _logger.info(
                                        f"[扫码保存数据] 处理包裹 {package_id}: 记录数={len(lines)}, "
                                        f"记录ID={[l.id for l in lines]}"
                                    )
                                    for line in lines:
                                        if line.lot_name:
                                            lot_name_normalized = line.lot_name.strip().lower()
                                            try:
                                                scan_index = scanned_lots.index(lot_name_normalized)
                                                # 设置扫描顺序（从1开始，不是从0开始）
                                                try:
                                                    current_scan_sequence = line.scan_sequence or 0
                                                    if current_scan_sequence != scan_index + 1:
                                                        line.with_context(skip_quantity_fix=True).write({'scan_sequence': scan_index + 1})
                                                        _logger.info(
                                                            f"[扫码保存数据] 包裹操作后，设置扫描顺序: 记录ID={line.id}, "
                                                            f"批次号={line.lot_name}, 扫描顺序={scan_index + 1}, "
                                                            f"包裹ID={package_id}"
                                                        )
                                                    else:
                                                        _logger.debug(
                                                            f"[扫码保存数据] 包裹操作后，扫描顺序已正确: 记录ID={line.id}, "
                                                            f"批次号={line.lot_name}, 扫描顺序={current_scan_sequence}, "
                                                            f"包裹ID={package_id}"
                                                        )
                                                except Exception as e:
                                                    _logger.warning(
                                                        f"[扫码保存数据] 包裹操作后，设置扫描顺序失败: 记录ID={line.id}, "
                                                        f"批次号={line.lot_name}, 包裹ID={package_id}, 错误={str(e)}", 
                                                        exc_info=True
                                                    )
                                            except ValueError:
                                                # 批次号不在扫描列表中，保持原有顺序
                                                _logger.warning(
                                                    f"[扫码保存数据] 包裹操作后，批次号不在扫描列表中: 记录ID={line.id}, "
                                                    f"批次号={line.lot_name}, 包裹ID={package_id}, 会话中的批次号={scanned_lots}"
                                                )
                            else:
                                _logger.warning(
                                    f"[扫码保存数据] 保存后处理包裹操作，但会话中没有批次号: picking_id={res_id}, "
                                    f"包裹数量={len(package_groups)}"
                                )
                        else:
                            _logger.debug(
                                f"[扫码保存数据] 保存后处理包裹操作，但没有找到包裹记录: picking_id={res_id}"
                            )
                    else:
                        _logger.warning(
                            f"[扫码保存数据] 保存后处理包裹操作，但 picking 不存在: res_id={res_id}"
                        )
                else:
                    _logger.debug(
                        f"[扫码保存数据] 保存后处理包裹操作，跳过: model={model}, res_id={res_id}"
                    )
            except Exception as e:
                _logger.error(
                    f"[扫码保存数据] 包裹操作后处理扫描顺序失败: {str(e)}", 
                    exc_info=True
                )
            
            # 返回保存结果
            return result
        except Exception as e:
            _logger.error(
                f"[扫码保存数据] 保存数据时出错: {str(e)}", 
                exc_info=True
            )
            raise
        
        # 如果不是 stock.picking 的 move_line_ids 写入，或者没有启用增强验证，调用父类方法
        return super(StockBarcodeController, self).save_barcode_data(
            model, res_id, write_field, write_vals, allow_duplicate_scan, **kwargs
        )

