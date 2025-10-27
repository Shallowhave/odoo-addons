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
        """获取批次号前缀，支持配置化"""
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
        批次号格式：{PREFIX}YYMMDDHHMMAxx[-X]
        - 同一制造单第一次注册生产：主批次
        - 欠单（同一 origin）：延续主批次，加 -2/-3
        - 不同制造单：Axx 递增
        """
        try:
            utc_now = fields.Datetime.now()
            user_dt = fields.Datetime.context_timestamp(self.env.user, utc_now)
            
            # 获取配置的前缀
            prefix = self._get_batch_prefix()
            date_str = user_dt.strftime('%y%m%d')
            time_str = user_dt.strftime('%H%M')
            Lot = self.env['stock.lot']

            # 首先检查是否是欠单
            if self._is_backorder():
                # 是欠单，查找主批次
                main_lot_for_origin = self._find_main_lot_for_production(Lot)
                if main_lot_for_origin:
                    # 找到主批次 → 生成分卷
                    return self._generate_sub_batch(main_lot_for_origin.name, Lot)
                else:
                    # 没找到主批次，按新批次处理
                    if self._is_logging_enabled():
                        _logger.warning("[自动批次] 欠单 %s 未找到主批次，按新批次处理", self.name)
                    return self._generate_main_batch(prefix, date_str, time_str, Lot)
            else:
                # 不是欠单，直接生成新的主批次
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
        
        # 提取已使用的序列号
        used_sequences = set()
        for lot in existing_lots:
            match = re.match(rf"^{re.escape(prefix)}\d{{6}}\d{{0,4}}A(\d{{2}})$", lot.name)
            if match:
                used_sequences.add(int(match.group(1)))
        
        # 找到下一个可用序列号
        next_seq = 1
        while next_seq in used_sequences and next_seq < 99:
            next_seq += 1
            
        if next_seq >= 99:
            raise UserError("当日批次号已满，请明天再试")
            
        lot_name = f"{prefix}{date_str}{time_str}A{next_seq:02d}"
        if self._is_logging_enabled():
            _logger.info("[自动批次] 生成主批次号：%s", lot_name)
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
        """优化后的批次号生成检查逻辑"""
        for production in self:
            try:
                # 检查是否所有组件就绪
                all_ready = all(move.state == 'assigned' for move in production.move_raw_ids)
                has_lot = bool(production.lot_producing_id)
                
                # 检查产品是否需要批次号
                if not production.product_id.tracking in ['lot', 'serial']:
                    _logger.debug("[AutoBatch] 产品 %s 不需要批次号", production.product_id.name)
                    continue

                if self._is_logging_enabled():
                    _logger.info("[自动批次] 检查制造单 %s - all_ready=%s has_lot=%s",
                                 production.name, all_ready, has_lot)

                if all_ready and not has_lot:
                    self._create_lot_for_production(production)
                    
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

