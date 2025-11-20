from odoo import models, fields, api
from datetime import datetime
import logging
import re
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    @api.model
    def _get_batch_prefix(self):
        """获取批次号前缀，支持配置化
        优先级：产品配置的前缀 > 全局配置的前缀
        """
        # 优先使用产品配置的前缀
        if self.product_id and self.product_id.mrp_lot_prefix:
            return self.product_id.mrp_lot_prefix
        
        # 回退到全局配置
        return self.env['ir.config_parameter'].sudo().get_param(
            'mrp_auto_lot_generate.batch_prefix', 'XQ'
        )
    
    @api.model
    def _is_logging_enabled(self):
        """检查是否启用详细日志"""
        return self.env['ir.config_parameter'].sudo().get_param(
            'mrp_auto_lot_generate.enable_logging', 'False'
        ).lower() == 'true'

    def _find_main_lot_for_production(self, Lot):
        """查找与当前制造单相关的主批次号"""
        # 方法1：通过 origin 字段查找
        if self.origin:
            main_lot = Lot.search([
                ('ref', '=', self.origin),
                ('name', 'not like', '%-%'),
                ('company_id', '=', self.company_id.id)
            ], limit=1)
            if main_lot:
                return main_lot
        
        # 方法2：通过制造单名称查找（处理欠单情况）
        # 欠单的名称通常包含原制造单的引用
        if self.name and '-' in self.name:
            # 尝试从制造单名称中提取原制造单名称
            base_name = self.name.split('-')[0]
            main_lot = Lot.search([
                ('ref', 'like', base_name),
                ('name', 'not like', '%-%'),
                ('company_id', '=', self.company_id.id)
            ], limit=1)
            if main_lot:
                return main_lot
        
        # 方法3：检查是否是欠单（通过检查制造单的关联关系）
        if hasattr(self, 'procurement_group_id') and self.procurement_group_id:
            # 查找同一采购组中的其他制造单
            related_productions = self.env['mrp.production'].search([
                ('procurement_group_id', '=', self.procurement_group_id.id),
                ('id', '!=', self.id),
                ('product_id', '=', self.product_id.id),
                ('company_id', '=', self.company_id.id)
            ])
            
            for production in related_productions:
                if production.lot_producing_id and '-' not in production.lot_producing_id.name:
                    return production.lot_producing_id
        
        return None

    def _is_backorder(self):
        """检查当前制造单是否是欠单"""
        # 只通过制造单名称来判断是否是欠单
        # 欠单的典型格式：WH/MO/00135-002, WH/MO/00136-004 等
        if self.name and '-' in self.name:
            # 检查是否是欠单格式（名称中包含数字-数字的模式）
            import re
            # 匹配模式：字母数字-数字（如 WH/MO/00135-002）
            pattern = r'^[A-Za-z0-9/]+-\d+$'
            if re.match(pattern, self.name):
                return True
        
        return False

    def _generate_batch_number(self):
        """
        优化后的批次号生成逻辑
        批次号格式：{PREFIX}YYMMDDHHMMAxx
        - 每个制造单（包括欠单）都生成独立的主批次号
        - 不再生成分卷批次号（-x后缀）
        """
        try:
            utc_now = fields.Datetime.now()
            user_dt = fields.Datetime.context_timestamp(self.env.user, utc_now)
            
            # 获取配置的前缀
            prefix = self._get_batch_prefix()
            date_str = user_dt.strftime('%y%m%d')
            time_str = user_dt.strftime('%H%M')
            Lot = self.env['stock.lot']

            # 所有订单都生成独立的主批次号（包括欠单）
            if self._is_logging_enabled():
                _logger.info("[自动批次] 为制造单 %s 生成独立批次号", self.name)
            
            return self._generate_main_batch(prefix, date_str, time_str, Lot)
                
        except Exception as e:
            _logger.error("[AutoBatch] 生成批次号失败: %s", str(e))
            raise UserError(f"生成批次号失败: {str(e)}")

    def _generate_main_batch(self, prefix, date_str, time_str, Lot):
        """生成主批次号"""
        # 优化：使用更精确的查询模式
        pattern = f"{prefix}{date_str}%A%"
        existing_lots = Lot.search([
            ('name', 'like', pattern),
            ('name', 'not like', '%-%'),
            ('company_id', '=', self.company_id.id)
        ])
        
        # 提取已使用的序列号（支持2位数和3位数）
        used_sequences = set()
        for lot in existing_lots:
            # 匹配 A01-A99 (2位数) 和 A100-A999 (3位数)
            match = re.match(rf"^{re.escape(prefix)}\d{{6}}\d{{0,4}}A(\d{{2,3}})$", lot.name)
            if match:
                used_sequences.add(int(match.group(1)))
        
        # 找到下一个可用序列号（从1开始，可以超过99）
        next_seq = 1
        max_retries = 999  # 最大支持到 A999
        retry_count = 0
        
        while next_seq in used_sequences and retry_count < max_retries:
            next_seq += 1
            retry_count += 1
            
        if retry_count >= max_retries:
            raise UserError(f"当日批次号序列已用完（已尝试到 {next_seq}），请明天再试")
        
        # 根据序列号位数格式化（A01-A99 用2位数，A100及以上用3位数）
        if next_seq <= 99:
            lot_name = f"{prefix}{date_str}{time_str}A{next_seq:02d}"
        else:
            lot_name = f"{prefix}{date_str}{time_str}A{next_seq:03d}"
        
        if self._is_logging_enabled():
            _logger.info("[自动批次] 生成主批次号：%s", lot_name)
        return lot_name

    def _generate_main_batch_for_product(self, prefix, date_str, time_str, Lot, product):
        """为指定产品生成主批次号（用于副产品独立批次号生成）"""
        # 优化：使用更精确的查询模式
        pattern = f"{prefix}{date_str}%A%"
        existing_lots = Lot.search([
            ('name', 'like', pattern),
            ('name', 'not like', '%-%'),
            ('product_id', '=', product.id),
            ('company_id', '=', self.company_id.id)
        ])
        
        # 提取已使用的序列号（支持2位数和3位数）
        used_sequences = set()
        for lot in existing_lots:
            # 匹配 A01-A99 (2位数) 和 A100-A999 (3位数)
            match = re.match(rf"^{re.escape(prefix)}\d{{6}}\d{{0,4}}A(\d{{2,3}})$", lot.name)
            if match:
                used_sequences.add(int(match.group(1)))
        
        # 找到下一个可用序列号（从1开始，可以超过99）
        next_seq = 1
        max_retries = 999  # 最大支持到 A999
        retry_count = 0
        
        while next_seq in used_sequences and retry_count < max_retries:
            next_seq += 1
            retry_count += 1
            
        if retry_count >= max_retries:
            raise UserError(f"产品 {product.display_name} 当日批次号序列已用完（已尝试到 {next_seq}），请明天再试")
        
        # 根据序列号位数格式化（A01-A99 用2位数，A100及以上用3位数）
        if next_seq <= 99:
            lot_name = f"{prefix}{date_str}{time_str}A{next_seq:02d}"
        else:
            lot_name = f"{prefix}{date_str}{time_str}A{next_seq:03d}"
        
        if self._is_logging_enabled():
            _logger.info("[自动批次] 为产品 %s 生成独立批次号：%s", product.display_name, lot_name)
        return lot_name

    def _generate_sub_batch(self, main_lot_name, Lot):
        """生成分卷批次号"""
        # 查找所有分卷批次号
        sub_lots = Lot.search([
            ('name', 'like', f'{main_lot_name}-%'),
            ('company_id', '=', self.company_id.id)
        ])
        
        # 提取已使用的分卷号
        used_sub_numbers = set()
        for lot in sub_lots:
            # 提取分卷号（-后面的数字）
            if '-' in lot.name:
                try:
                    sub_num = int(lot.name.split('-')[-1])
                    used_sub_numbers.add(sub_num)
                except ValueError:
                    continue
        
        # 找到下一个可用的分卷号，从1开始
        next_sub = 1
        while next_sub in used_sub_numbers and next_sub < 99:
            next_sub += 1
            
        if next_sub >= 99:
            raise UserError(f"主批次 {main_lot_name} 的分卷数量已满")
            
        lot_name = f"{main_lot_name}-{next_sub}"
        if self._is_logging_enabled():
            _logger.info("[自动批次] 生成分卷批次号：%s", lot_name)
        return lot_name

    def _try_generate_lot(self):
        """优化后的批次号生成检查逻辑（包括主产品和副产品）"""
        for production in self:
            try:
                # 检查是否所有组件就绪
                all_ready = all(move.state == 'assigned' for move in production.move_raw_ids)
                has_lot = bool(production.lot_producing_id)
                
                # 检查主产品是否需要批次号
                if production.product_id.tracking in ['lot', 'serial']:
                    if self._is_logging_enabled():
                        _logger.info("[自动批次] 检查制造单 %s - all_ready=%s has_lot=%s",
                                     production.name, all_ready, has_lot)

                    if all_ready and not has_lot:
                        self._create_lot_for_production(production)
                else:
                    _logger.debug("[AutoBatch] 主产品 %s 不需要批次号", production.product_id.name)
                
                # 检查并生成副产品的批次号
                if all_ready:
                    self._try_generate_byproduct_lots(production)
                    
            except Exception as e:
                _logger.error("[自动批次] 为制造单 %s 生成批次号失败: %s", 
                             production.name, str(e))
                # 不抛出异常，避免影响其他制造单

    def _create_lot_for_production(self, production):
        """为制造单创建批次号"""
        lot_name = production._generate_batch_number()
        
        # 检查批次号是否已存在
        existing_lot = self.env['stock.lot'].search([
            ('name', '=', lot_name),
            ('company_id', '=', production.company_id.id)
        ], limit=1)
        
        if existing_lot:
            _logger.warning("[自动批次] 批次号 %s 已存在，跳过创建", lot_name)
            production.lot_producing_id = existing_lot.id
            return
            
        lot = self.env['stock.lot'].create({
            'name': lot_name,
            'product_id': production.product_id.id,
            'company_id': production.company_id.id,
            'ref': production.origin or production.name,
        })
        
        production.lot_producing_id = lot.id
        if self._is_logging_enabled():
            _logger.info("[自动批次] 批次号 %s 已绑定到制造单 %s", lot_name, production.name)

    def _try_generate_byproduct_lots(self, production):
        """为制造单的副产品生成批次号"""
        if not hasattr(production, 'move_byproduct_ids'):
            return
        
        byproduct_moves = production.move_byproduct_ids.filtered(
            lambda m: m.state != 'cancel' and m.product_id.tracking in ['lot', 'serial']
        )
        
        if not byproduct_moves:
            if self._is_logging_enabled():
                _logger.debug("[自动批次] 制造单 %s 没有需要批次号的副产品", production.name)
            return
        
        for byproduct_move in byproduct_moves:
            try:
                # 检查副产品移动是否已有批次号
                has_byproduct_lot = False
                if byproduct_move.move_line_ids:
                    # 检查移动行是否已有批次号
                    has_byproduct_lot = any(
                        line.lot_id for line in byproduct_move.move_line_ids
                    )
                
                if not has_byproduct_lot:
                    if self._is_logging_enabled():
                        _logger.info("[自动批次] 为副产品 %s 生成批次号（制造单：%s）",
                                     byproduct_move.product_id.display_name, production.name)
                    self._create_lot_for_byproduct(production, byproduct_move)
                else:
                    if self._is_logging_enabled():
                        _logger.debug("[自动批次] 副产品 %s 已有批次号，跳过生成",
                                     byproduct_move.product_id.display_name)
            except Exception as e:
                _logger.error("[自动批次] 为副产品 %s 生成批次号失败: %s",
                             byproduct_move.product_id.display_name, str(e))
                # 不抛出异常，继续处理其他副产品

    def _create_lot_for_byproduct(self, production, byproduct_move):
        """为副产品创建批次号
        逻辑：
        1. 如果副产品配置了自己的前缀，生成独立的批次号
        2. 否则，基于主产品批次号添加后缀（-B, -C等）
        """
        byproduct = byproduct_move.product_id
        
        # 检查副产品是否配置了自己的前缀
        if byproduct.mrp_lot_prefix:
            # 副产品有自己的前缀，生成独立的批次号
            if self._is_logging_enabled():
                _logger.info("[自动批次] 副产品 %s 配置了专属前缀 %s，生成独立批次号",
                             byproduct.display_name, byproduct.mrp_lot_prefix)
            
            # 临时创建一个虚拟的制造单对象来生成批次号
            # 使用副产品的信息
            utc_now = fields.Datetime.now()
            user_dt = fields.Datetime.context_timestamp(self.env.user, utc_now)
            prefix = byproduct.mrp_lot_prefix
            date_str = user_dt.strftime('%y%m%d')
            time_str = user_dt.strftime('%H%M')
            Lot = self.env['stock.lot']
            
            # 生成独立的批次号（使用副产品的前缀）
            lot_name = self._generate_main_batch_for_product(prefix, date_str, time_str, Lot, byproduct)
        else:
            # 副产品没有配置前缀，使用主产品批次号作为基础生成副产品批次号
            if production.lot_producing_id:
                base_lot_name = production.lot_producing_id.name
            else:
                # 如果主产品还没有批次号，先为主产品生成一个基础批次号（不创建，仅用于生成副产品批次号）
                base_lot_name = production._generate_batch_number()
            
            # 生成带后缀的副产品批次号
            lot_name = self._generate_byproduct_batch_with_suffix(
                production, byproduct, base_lot_name
            )
        
        # 检查批次号是否已存在
        existing_lot = self.env['stock.lot'].search([
            ('name', '=', lot_name),
            ('product_id', '=', byproduct_move.product_id.id),
            ('company_id', '=', production.company_id.id)
        ], limit=1)
        
        if existing_lot:
            _logger.warning("[自动批次] 副产品批次号 %s 已存在，使用现有批次号", lot_name)
            lot = existing_lot
        else:
            # 创建新的批次号
            lot = self.env['stock.lot'].create({
                'name': lot_name,
                'product_id': byproduct_move.product_id.id,
                'company_id': production.company_id.id,
                'ref': f"{production.origin or production.name} - 副产品",
            })
        
        # 将批次号关联到副产品的移动行
        # 如果移动行已存在，直接更新批次号
        if byproduct_move.move_line_ids:
            for move_line in byproduct_move.move_line_ids:
                if not move_line.lot_id:
                    move_line.lot_id = lot.id
        # 如果移动行不存在，批次号会在移动行创建时通过上下文或其他机制关联
        # 这里我们先将批次号存储在移动的上下文中，以便后续使用
        # 注意：Odoo标准流程会在适当时机创建移动行
        
        if self._is_logging_enabled():
            _logger.info("[自动批次] 副产品批次号 %s 已绑定到副产品 %s（制造单：%s）",
                         lot_name, byproduct_move.product_id.display_name, production.name)

    def _generate_byproduct_batch_with_suffix(self, production, product, base_lot_name):
        """为副产品生成带后缀的批次号（基于主产品批次号）"""
        Lot = self.env['stock.lot']
        
        # 提取基础批次号（去掉可能的后缀）
        # 格式：XQYYMMDDHHMMAxx 或 XQYYMMDDHHMMAxx-B
        base_pattern = base_lot_name.split('-')[0] if '-' in base_lot_name else base_lot_name
        
        # 查找所有以基础批次号开头且属于同一产品的批次号
        # 这样可以找到同一制造单的其他副产品批次号
        existing_lots = Lot.search([
            ('name', 'like', f'{base_pattern}-%'),
            ('product_id', '=', product.id),
            ('company_id', '=', production.company_id.id)
        ])
        
        # 提取已使用的后缀（字母或数字）
        used_letter_suffixes = set()
        used_number_suffixes = set()
        
        for lot in existing_lots:
            if '-' in lot.name:
                try:
                    suffix = lot.name.split('-')[-1].strip()
                    # 检查是字母后缀还是数字后缀
                    if len(suffix) == 1 and suffix.isalpha():
                        # 单个字母后缀（B, C, D, ...）
                        used_letter_suffixes.add(suffix.upper())
                    elif suffix.isdigit():
                        # 数字后缀（01, 02, ...）
                        used_number_suffixes.add(int(suffix))
                except (ValueError, IndexError):
                    continue
        
        # 优先使用字母后缀（从B开始）
        next_letter = 'B'
        while next_letter in used_letter_suffixes and ord(next_letter) < ord('Z'):
            next_letter = chr(ord(next_letter) + 1)
        
        if next_letter <= 'Z' and next_letter not in used_letter_suffixes:
            # 使用字母后缀
            lot_name = f"{base_pattern}-{next_letter}"
        else:
            # 如果字母用完了，使用数字后缀（从01开始）
            next_number = 1
            while next_number in used_number_suffixes and next_number < 99:
                next_number += 1
            if next_number >= 99:
                raise UserError(f"副产品 {product.display_name} 的批次号后缀已用完（已尝试到 {next_number}）")
            lot_name = f"{base_pattern}-{next_number:02d}"
        
        if self._is_logging_enabled():
            _logger.info("[自动批次] 为副产品 %s 生成带后缀的批次号：%s（基于主产品批次号：%s）",
                         product.display_name, lot_name, base_pattern)
        
        return lot_name


class StockMove(models.Model):
    _inherit = 'stock.move'

    def write(self, vals):
        """优化后的组件状态变化监听"""
        res = super().write(vals)
        
        # 只在状态变为 assigned 时触发检查
        if 'state' in vals and vals['state'] == 'assigned':
            self._check_production_lot_generation()
            
        return res

    def _check_production_lot_generation(self):
        """检查制造单是否需要生成批次号"""
        for move in self:
            if not move.raw_material_production_id:
                continue
                
            production = move.raw_material_production_id
            
            # 跳过已取消或完成的制造单
            if production.state in ['cancel', 'done']:
                continue
                
            if production._is_logging_enabled():
                _logger.info("[自动批次] 组件 %s 状态变为 assigned，检查制造单 %s",
                             move.product_id.display_name, production.name)
            
            try:
                production._try_generate_lot()
            except Exception as e:
                _logger.error("[自动批次] 检查制造单 %s 批次号生成失败: %s", 
                             production.name, str(e))


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        """扩展创建方法，自动为副产品移动行关联预生成的批次号"""
        # 先调用父类方法创建记录
        move_lines = super().create(vals_list)
        
        # 为每个新创建的移动行检查是否需要关联副产品批次号
        for move_line in move_lines:
            if move_line.lot_id:
                # 如果已有批次号，跳过
                continue
            
            move = move_line.move_id
            if not move or not hasattr(move, 'production_id'):
                continue
            
            production = move.production_id
            if not production:
                continue
            
            # 检查是否是副产品移动
            if hasattr(production, 'move_byproduct_ids') and move in production.move_byproduct_ids:
                # 查找该副产品移动对应的批次号
                if move.product_id.tracking in ['lot', 'serial']:
                    # 查找该副产品产品的批次号（基于主产品批次号）
                    if production.lot_producing_id:
                        base_lot_name = production.lot_producing_id.name
                        # 查找匹配的副产品批次号
                        byproduct_lot = self.env['stock.lot'].search([
                            ('name', 'like', f'{base_lot_name.split("-")[0]}-%'),
                            ('product_id', '=', move.product_id.id),
                            ('company_id', '=', production.company_id.id),
                            ('ref', 'like', f'%{production.name}%'),
                        ], limit=1)
                        
                        if byproduct_lot and not move_line.lot_id:
                            move_line.lot_id = byproduct_lot.id
                            if production._is_logging_enabled():
                                _logger.info("[自动批次] 自动关联副产品批次号 %s 到移动行（制造单：%s）",
                                             byproduct_lot.name, production.name)
        
        return move_lines

