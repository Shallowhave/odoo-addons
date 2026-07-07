from odoo import models, fields, api, _
import logging
import re
from psycopg2 import errors

from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    mrp_auto_lot_name = fields.Char(
        string='批次/序列号',
        compute='_compute_mrp_auto_lot_name',
        inverse='_inverse_mrp_auto_lot_name',
        readonly=False,
    )

    @api.depends('lot_producing_id.name')
    def _compute_mrp_auto_lot_name(self):
        for production in self:
            production.mrp_auto_lot_name = production.lot_producing_id.name or False

    def _inverse_mrp_auto_lot_name(self):
        for production in self:
            production._set_lot_producing_name_from_form(production.mrp_auto_lot_name)

    def _set_lot_producing_name_from_form(self, lot_name):
        self.ensure_one()
        if not self.lot_producing_id:
            return

        lot_name = (lot_name or '').strip()
        if not lot_name:
            raise ValidationError(_('批次/序列号不能为空。'))

        if self.lot_producing_id.name != lot_name:
            self.lot_producing_id.write({'name': lot_name})

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
    
    @api.model
    def _is_override_generate_serial_enabled(self):
        """检查是否启用覆盖原生批次号生成"""
        return self.env['ir.config_parameter'].sudo().get_param(
            'mrp_auto_lot_generate.override_generate_serial', 'True'
        ).lower() == 'true'

    @api.model
    def _is_prefix_date_only_enabled(self):
        """是否只生成前缀和日期，后续由业务人员填写"""
        return self.env['ir.config_parameter'].sudo().get_param(
            'mrp_auto_lot_generate.prefix_date_only', 'False'
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

            if self._is_prefix_date_only_enabled():
                return self._generate_prefix_date_batch(prefix, date_str)
            
            return self._generate_main_batch(prefix, date_str, time_str, Lot)
                
        except Exception as e:
            _logger.error("[AutoBatch] 生成批次号失败: %s", str(e))
            raise UserError(f"生成批次号失败: {str(e)}")

    def _generate_prefix_date_batch(self, prefix, date_str):
        """只生成批次号的前缀和日期部分"""
        lot_name = f"{prefix}{date_str}"
        if self._is_logging_enabled():
            _logger.info("[自动批次] 只生成批次号前缀和日期：%s", lot_name)
        return lot_name

    def _lock_lot_generation(self):
        """串行化同公司、同前缀、同日期的批次号生成。"""
        self.ensure_one()
        utc_now = fields.Datetime.now()
        user_dt = fields.Datetime.context_timestamp(self.env.user, utc_now)
        lock_key = '%s:%s:%s' % (
            self.company_id.id or 0,
            self._get_batch_prefix(),
            user_dt.strftime('%y%m%d'),
        )
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
            [lock_key],
        )

    def _get_committed_lot_names_for_generation(self, pattern, product=None):
        """用新游标读取最新已提交的批号名，避免当前事务旧快照漏读。"""
        where_clauses = [
            "name LIKE %s",
            "name NOT LIKE %s",
        ]
        params = [pattern, '%-%']

        if self.company_id:
            where_clauses.append("company_id = %s")
            params.append(self.company_id.id)
        else:
            where_clauses.append("company_id IS NULL")

        if product:
            where_clauses.append("product_id = %s")
            params.append(product.id)

        query = "SELECT name FROM stock_lot WHERE %s" % " AND ".join(where_clauses)
        with self.env.registry.cursor() as cr:
            cr.execute(query, params)
            return [row[0] for row in cr.fetchall()]

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
        existing_names = set(existing_lots.mapped('name'))
        existing_names.update(self._get_committed_lot_names_for_generation(pattern))
        for lot_name in existing_names:
            # 匹配 A01-A99 (2位数) 和 A100-A999 (3位数)
            match = re.match(rf"^{re.escape(prefix)}\d{{6}}\d{{0,4}}A(\d{{2,3}})$", lot_name)
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
        existing_names = set(existing_lots.mapped('name'))
        existing_names.update(self._get_committed_lot_names_for_generation(pattern, product=product))
        for lot_name in existing_names:
            # 匹配 A01-A99 (2位数) 和 A100-A999 (3位数)
            match = re.match(rf"^{re.escape(prefix)}\d{{6}}\d{{0,4}}A(\d{{2,3}})$", lot_name)
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
        production._lock_lot_generation()
        prefix_date_only = production._is_prefix_date_only_enabled()
        max_attempts = 1 if prefix_date_only else 5
        last_error = None

        for attempt in range(max_attempts):
            lot_name = production._generate_batch_number()
            try:
                with self.env.cr.savepoint():
                    if not prefix_date_only:
                        # 检查批次号是否已存在
                        existing_lot = self.env['stock.lot'].search([
                            ('name', '=', lot_name),
                            ('company_id', '=', production.company_id.id)
                        ], limit=1)

                        if existing_lot:
                            raise ValidationError(_(
                                '批次/序列号 "%(lot_name)s" 已存在，不能重复使用。'
                            ) % {'lot_name': lot_name})

                    lot = self.env['stock.lot'].create({
                        'name': lot_name,
                        'product_id': production.product_id.id,
                        'company_id': production.company_id.id,
                        'ref': production.origin or production.name,
                        'mrp_auto_lot_needs_suffix': prefix_date_only,
                        'mrp_auto_lot_production_id': production.id,
                    })
                production.lot_producing_id = lot.id
                if self._is_logging_enabled():
                    _logger.info("[自动批次] 批次号 %s 已绑定到制造单 %s", lot_name, production.name)
                return
            except (ValidationError, errors.UniqueViolation) as error:
                last_error = error
                if prefix_date_only:
                    raise
                _logger.warning(
                    "[自动批次] 批次号 %s 创建冲突，准备重试（第 %s/%s 次）: %s",
                    lot_name, attempt + 1, max_attempts, error
                )

        raise UserError(_('无法生成唯一批次号，请稍后重试。最后错误：%s') % last_error)

    def _check_lot_producing_id_selectable(self, lot):
        """Prevent manually selecting an existing lot as the finished lot of an MO."""
        self.ensure_one()
        if not lot:
            return

        source_production = lot.mrp_auto_lot_production_id
        if source_production:
            if source_production != self:
                raise ValidationError(_(
                    '批次/序列号 "%(lot_name)s" 已由制造订单 "%(production)s" 生成，不能用于当前制造订单。'
                ) % {
                    'lot_name': lot.name,
                    'production': source_production.display_name,
                })
            return

        raise ValidationError(_(
            '批次/序列号 "%(lot_name)s" 已存在，不能直接选用已有批次号。'
            '请点击“创建新序列号/批号”生成当前制造订单的批次号，或在当前批次号上补全后缀。'
        ) % {'lot_name': lot.name})

    def _check_prefix_date_lot_completed(self):
        for production in self.filtered('lot_producing_id'):
            if production.lot_producing_id.mrp_auto_lot_needs_suffix:
                raise ValidationError(_(
                    '制造订单 "%(production)s" 的批次/序列号 "%(lot_name)s" 还只有前缀和日期，'
                    '请先补全后缀再完成生产。'
                ) % {
                    'production': production.display_name,
                    'lot_name': production.lot_producing_id.name,
                })

    def _check_finished_lot_available(self, lot, product=None, exclude_move_lines=None):
        """Ensure a finished-product lot is not reused by another manufacturing order."""
        self.ensure_one()
        if not lot:
            return

        if product and lot.product_id and lot.product_id != product:
            raise ValidationError(_(
                '批次/序列号 "%(lot_name)s" 属于产品 "%(lot_product)s"，不能用于产品 "%(product)s"。'
            ) % {
                'lot_name': lot.name,
                'lot_product': lot.product_id.display_name,
                'product': product.display_name,
            })

        if self.company_id and lot.company_id and lot.company_id != self.company_id:
            raise ValidationError(_(
                '批次/序列号 "%(lot_name)s" 属于公司 "%(lot_company)s"，不能用于当前制造订单。'
            ) % {
                'lot_name': lot.name,
                'lot_company': lot.company_id.display_name,
            })

        conflict_production = self.sudo().search([
            ('lot_producing_id', '=', lot.id),
            ('id', '!=', self.id),
            ('state', '!=', 'cancel'),
        ], limit=1)
        if conflict_production:
            raise ValidationError(_(
                '批次/序列号 "%(lot_name)s" 已被制造订单 "%(production)s" 使用，不能重复使用。'
            ) % {
                'lot_name': lot.name,
                'production': conflict_production.display_name,
            })

        move_line_domain = [
            ('lot_id', '=', lot.id),
            ('state', '!=', 'cancel'),
            ('move_id.production_id', '!=', False),
            ('move_id.picking_id', '=', False),
            ('picking_id', '=', False),
        ]
        if product:
            move_line_domain.append(('product_id', '=', product.id))
        if exclude_move_lines:
            move_line_domain.append(('id', 'not in', exclude_move_lines.ids))

        for move_line in self.env['stock.move.line'].sudo().search(move_line_domain):
            line_production = move_line.move_id.production_id
            if line_production and line_production.id != self.id and line_production.state != 'cancel':
                raise ValidationError(_(
                    '批次/序列号 "%(lot_name)s" 已被制造订单 "%(production)s" 的生产明细使用，不能重复使用。'
                ) % {
                    'lot_name': lot.name,
                    'production': line_production.display_name,
                })

    @api.constrains('lot_producing_id', 'product_id', 'company_id')
    def _check_lot_producing_id_not_reused(self):
        for production in self.filtered('lot_producing_id'):
            production._check_finished_lot_available(
                production.lot_producing_id,
                product=production.product_id,
            )

    @api.model_create_multi
    def create(self, vals_list):
        productions = super().create(vals_list)
        if not self.env.context.get('skip_mrp_auto_lot_selectable_check'):
            for production, vals in zip(productions, vals_list):
                if vals.get('lot_producing_id'):
                    production._check_lot_producing_id_selectable(production.lot_producing_id)
        return productions

    def write(self, vals):
        if (
            not self.env.context.get('skip_mrp_auto_lot_selectable_check')
            and 'lot_producing_id' in vals
            and vals.get('lot_producing_id')
        ):
            lot = self.env['stock.lot'].browse(vals['lot_producing_id']).exists()
            for production in self:
                if lot and lot != production.lot_producing_id:
                    production._check_lot_producing_id_selectable(lot)
        return super().write(vals)

    def button_mark_done(self):
        self._check_prefix_date_lot_completed()
        return super().button_mark_done()
    
    def action_create_lot_producing(self):
        """手动创建批次号按钮动作
        当用户点击"创建新序列号/批号"按钮时调用此方法
        """
        self.ensure_one()
        
        # 检查产品是否需要批次号
        if self.product_id.tracking not in ['lot', 'serial']:
            raise UserError(_('产品 %s 不需要批次号/序列号') % self.product_id.display_name)
        
        # 如果已经有批次号，提示用户
        if self.lot_producing_id:
            raise UserError(_('制造订单 %s 已有批次号：%s') % (self.name, self.lot_producing_id.name))
        
        # 调用生成批次号的方法
        try:
            self._create_lot_for_production(self)
            
            # 返回成功消息
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('批次号生成成功'),
                    'message': _('已为制造订单 %s 生成批次号：%s') % (self.name, self.lot_producing_id.name),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_('生成批次号失败：%s') % str(e))
    
    def action_generate_serial(self):
        """覆盖原生的 action_generate_serial 方法，使用我们的自动批次号生成逻辑"""
        self.ensure_one()
        
        # 检查是否启用覆盖原生批次号生成
        if not self._is_override_generate_serial_enabled():
            # 如果未启用，使用原生方法
            return super(
                MrpProduction,
                self.with_context(skip_mrp_auto_lot_selectable_check=True),
            ).action_generate_serial()
        
        # 检查产品是否需要批次号
        if self.product_id.tracking not in ['lot', 'serial']:
            # 如果不需要批次号，调用父类方法
            return super(
                MrpProduction,
                self.with_context(skip_mrp_auto_lot_selectable_check=True),
            ).action_generate_serial()
        
        # 如果已经有批次号，提示用户
        if self.lot_producing_id:
            raise UserError(_('制造订单 %s 已有批次号：%s') % (self.name, self.lot_producing_id.name))
        
        # 使用我们的自动生成逻辑
        try:
            # 直接调用 _create_lot_for_production，它会设置 lot_producing_id
            self._create_lot_for_production(self)
            
            # 不返回任何动作，让前端自动更新显示（就像原生方法一样）
            # 前端会检测到 lot_producing_id 字段的变化并自动更新显示
            return True
        except Exception as e:
            raise UserError(_('生成批次号失败：%s') % str(e))

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
        1. 从主产品批次号中提取日期时间序列部分（去掉前缀）
        2. 如果副产品配置了自己的前缀，使用副产品的前缀；否则使用主产品的前缀
        3. 组合：副产品前缀 + 主产品的日期时间序列 + -(A-Z) 后缀
        格式：{副产品前缀}YYMMDDHHMMAxx-x
        示例：主产品 XQ2412011200A01，副产品前缀MQ -> MQ2412011200A01-A
        """
        byproduct = byproduct_move.product_id
        
        # 获取主产品批次号
        if production.lot_producing_id:
            main_lot_name = production.lot_producing_id.name
        else:
            # 如果主产品还没有批次号，先为主产品生成一个基础批次号（不创建，仅用于生成副产品批次号）
            main_lot_name = production._generate_batch_number()
            if self._is_logging_enabled():
                _logger.info("[自动批次] 主产品还没有批次号，生成临时批次号用于副产品：%s", main_lot_name)
        
        # **优化**：从主产品批次号中提取日期时间序列部分（去掉前缀）
        # 主产品批次号格式：{PREFIX}YYMMDDHHMMAxx
        # 例如：XQ2412011200A01 -> 提取 2412011200A01
        main_lot_name_clean = main_lot_name.split('-')[0] if '-' in main_lot_name else main_lot_name
        
        # 提取主产品的前缀（假设前缀是2-3个字母）
        # 尝试匹配：前缀 + 日期时间序列（YYMMDDHHMMAxx）
        # 日期时间序列格式：6位日期 + 4位时间 + A + 2-3位序列号
        main_prefix_match = re.match(r'^([A-Za-z]{2,3})(\d{6}\d{0,4}A\d{2,3})$', main_lot_name_clean)
        if main_prefix_match:
            main_prefix = main_prefix_match.group(1)
            date_time_sequence = main_prefix_match.group(2)  # YYMMDDHHMMAxx
        else:
            # 如果无法解析，尝试简单方式：去掉前2-3个字符作为前缀
            # 假设前缀是2-3个字符
            if len(main_lot_name_clean) > 5:
                main_prefix = main_lot_name_clean[:2]  # 默认2个字符前缀
                date_time_sequence = main_lot_name_clean[2:]
            else:
                # 如果批次号太短，使用原样
                main_prefix = ''
                date_time_sequence = main_lot_name_clean
        
        # 确定副产品使用的前缀
        if byproduct.mrp_lot_prefix:
            # 副产品配置了自己的前缀，使用副产品的前缀
            byproduct_prefix = byproduct.mrp_lot_prefix
            if self._is_logging_enabled():
                _logger.info("[自动批次] 副产品 %s 配置了专属前缀 %s，使用该前缀",
                             byproduct.display_name, byproduct_prefix)
        else:
            # 副产品没有配置前缀，使用主产品的前缀
            byproduct_prefix = main_prefix
            if self._is_logging_enabled():
                _logger.info("[自动批次] 副产品 %s 没有配置前缀，使用主产品前缀 %s",
                             byproduct.display_name, byproduct_prefix)
        
        # 组合基础批次号：副产品前缀 + 主产品的日期时间序列
        base_lot_name = f"{byproduct_prefix}{date_time_sequence}"
        
        if self._is_logging_enabled():
            _logger.info("[自动批次] 为副产品 %s 生成批次号，前缀=%s，日期时间序列=%s，基础批次号=%s",
                         byproduct.display_name, byproduct_prefix, date_time_sequence, base_lot_name)
        
        # 生成带后缀的副产品批次号（格式：副产品前缀+主产品日期时间序列-A）
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
            raise ValidationError(_(
                '副产品批次/序列号 "%(lot_name)s" 已存在，不能重复使用。'
            ) % {'lot_name': lot_name})
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
        """为副产品生成带后缀的批次号（基于主产品批次号）
        
        格式：xxxxxxxx-x，其中 xxxxxxxx 是主产品的批次号，x 是后缀（A-Z 或 01-99）
        """
        Lot = self.env['stock.lot']
        
        # **优化**：提取基础批次号（去掉可能的后缀）
        # 格式：XQYYMMDDHHMMAxx 或 XQYYMMDDHHMMAxx-B
        # 确保使用主产品的完整批次号作为基础
        base_pattern = base_lot_name.split('-')[0] if '-' in base_lot_name else base_lot_name
        
        # **优化**：查找所有以基础批次号开头且属于同一制造单的所有副产品批次号
        # 不限制产品，这样可以确保同一制造单的所有副产品使用统一的后缀序列
        # 查找同一制造单的所有副产品移动
        all_byproduct_moves = production.move_byproduct_ids
        all_byproduct_product_ids = all_byproduct_moves.mapped('product_id.id') if all_byproduct_moves else []
        
        # 查找所有以基础批次号开头的副产品批次号（同一制造单的所有副产品）
        domain = [
            ('name', 'like', f'{base_pattern}-%'),
            ('company_id', '=', production.company_id.id)
        ]
        if all_byproduct_product_ids:
            domain.append(('product_id', 'in', all_byproduct_product_ids))
        
        existing_lots = Lot.search(domain)
        
        # 提取已使用的后缀（字母或数字）
        used_letter_suffixes = set()
        used_number_suffixes = set()
        
        for lot in existing_lots:
            if '-' in lot.name:
                try:
                    suffix = lot.name.split('-')[-1].strip()
                    # 检查是字母后缀还是数字后缀
                    if len(suffix) == 1 and suffix.isalpha():
                        # 单个字母后缀（A, B, C, D, ...），统一转换为大写
                        used_letter_suffixes.add(suffix.upper())
                    elif suffix.isdigit():
                        # 数字后缀（01, 02, ...）
                        used_number_suffixes.add(int(suffix))
                except (ValueError, IndexError):
                    continue
        
        # 优先使用字母后缀（从A开始，A-Z）
        next_letter = 'A'
        while next_letter in used_letter_suffixes and ord(next_letter) < ord('Z'):
            next_letter = chr(ord(next_letter) + 1)
        
        if next_letter <= 'Z' and next_letter not in used_letter_suffixes:
            # 使用字母后缀（确保是大写）
            lot_name = f"{base_pattern}-{next_letter.upper()}"
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

    def _is_mrp_finished_move_line(self):
        self.ensure_one()
        return bool(
            self.move_id
            and self.move_id.production_id
            and not self.move_id.picking_id
            and not self.picking_id
        )

    def _get_finished_production(self):
        self.ensure_one()
        if self._is_mrp_finished_move_line():
            return self.move_id.production_id
        return self.env['mrp.production']

    def _check_finished_lot_not_reused(self):
        for move_line in self.filtered(lambda line: line.lot_id and line.state != 'cancel'):
            production = move_line._get_finished_production()
            if not production:
                continue
            production._check_finished_lot_available(
                move_line.lot_id,
                product=move_line.product_id,
                exclude_move_lines=move_line,
            )

    @api.constrains('lot_id', 'product_id', 'move_id', 'production_id', 'state')
    def _check_finished_lot_id_not_reused(self):
        self._check_finished_lot_not_reused()

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

        move_lines._check_finished_lot_not_reused()
        return move_lines

    def write(self, vals):
        res = super().write(vals)
        if {'lot_id', 'product_id', 'move_id', 'production_id', 'state'} & set(vals):
            self._check_finished_lot_not_reused()
        return res
