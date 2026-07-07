# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round
import math
import logging

_logger = logging.getLogger(__name__)


class QualityCheck(models.Model):
    _inherit = 'quality.check'
    
    # 测试类型字段
    test_type = fields.Char(
        string='测试类型',
        related='point_id.test_type_id.technical_name',
        readonly=True,
        store=False,
        help='质检点的技术名称，用于判断是否为组件扫码确认测试'
    )
    
    # 选择的待登记组件（用户先选择要验证的组件）
    selected_component_id = fields.Many2one(
        'product.product',
        string='选择的待登记组件',
        help='用户选择的待登记组件，需要验证扫码的产品是否匹配此组件'
    )
    
    # 扫码验证的组件信息
    scanned_component_id = fields.Many2one(
        'product.product',
        string='扫码的组件',
        help='通过扫码验证的组件产品'
    )
    
    scanned_component_code = fields.Char(
        string='扫码的组件编码',
        help='扫码获取的组件编码（条码/批次号）'
    )
    
    # 扫码的批次号
    scanned_lot_id = fields.Many2one(
        'stock.lot',
        string='扫码的批次号',
        help='通过扫码获取的批次号'
    )
    
    selected_move_line_id = fields.Many2one(
        'stock.move.line',
        string='待登记组件移动行',
        help='用户选择的具体待登记组件移动行，用于按批次/移动行粒度验证。'
    )

    # 待登记组件对应的批次号（从生产订单移动行获取）
    selected_lot_id = fields.Many2one(
        'stock.lot',
        string='待登记组件批次号',
        compute='_compute_selected_lot_id',
        store=False,
        help='待登记组件对应的批次号，从选择的移动行中获取'
    )
    
    component_verification_result = fields.Selection([
        ('pending', '待验证'),
        ('matched', '匹配'),
        ('mismatched', '不匹配'),
    ], string='验证结果', default='pending', readonly=True)
    
    component_verification_message = fields.Text(
        string='验证消息',
        help='组件验证的详细消息'
    )
    
    @api.model_create_multi
    def create(self, vals_list):
        """
        创建质检记录时，如果质检点配置了 component_id，则自动设置到 selected_component_id
        参考 register_consumed_materials 的实现
        支持批量创建（Odoo 18 推荐）
        """
        records = super(QualityCheck, self).create(vals_list)
        
        # 批量处理：如果质检点配置了 component_id，则自动设置到 selected_component_id
        records_to_update = records.filtered(
            lambda r: not r.selected_component_id 
            and r.point_id 
            and r.point_id.component_id 
            and r.point_id.test_type_id.technical_name == 'component_scan_verify'
        )
        if records_to_update:
            for record in records_to_update:
                record.selected_component_id = record.point_id.component_id.id
        
        return records
    
    def _raise_uom_configuration_error(self, title, details):
        raise UserError(_(
            '%(title)s\n\n'
            '%(details)s\n\n'
            '请由库存/产品管理员修正计量单位配置后再继续质检。'
        ) % {
            'title': title,
            'details': details,
        })

    def _ensure_positive_uom_rounding(self, uom, source):
        """Validate UOM precision without changing shared master data."""
        if uom and (not uom.rounding or uom.rounding <= 0):
            self._raise_uom_configuration_error(
                _('计量单位舍入精度无效，无法通过质检。'),
                _('位置：%(source)s\n计量单位：%(uom)s\n当前舍入精度：%(rounding)s') % {
                    'source': source,
                    'uom': uom.display_name,
                    'rounding': uom.rounding,
                }
            )

    def _ensure_product_uom_consistency(self, product, move_uom):
        """
        校验产品的 UOM 与移动行的 UOM 一致，不在质检流程中自动修改主数据。
        
        :param product: product.product 记录
        :param move_uom: uom.uom 记录（来自移动行）
        :return: False（保持兼容调用方的布尔判断）
        """
        if not product or not move_uom:
            return False
        
        needs_fix = False
        fix_reason = ''
        
        # 检查产品变体的 UOM
        if not product.uom_id:
            needs_fix = True
            fix_reason = '产品变体没有 UOM'
        elif product.uom_id.category_id != move_uom.category_id:
            needs_fix = True
            fix_reason = '产品变体 UOM 类别不匹配'
        
        # 检查产品模板的 UOM
        tmpl = product.product_tmpl_id
        if tmpl:
            tmpl_uom = tmpl.uom_id
            if not tmpl_uom:
                needs_fix = True
                fix_reason = fix_reason or '产品模板没有 UOM'
            elif tmpl_uom.category_id != move_uom.category_id:
                needs_fix = True
                fix_reason = fix_reason or '产品模板 UOM 类别不匹配'
        
        self._ensure_positive_uom_rounding(move_uom, _('生产订单组件移动'))

        if needs_fix:
            self._raise_uom_configuration_error(
                _('产品计量单位配置异常，无法通过质检。'),
                _('产品：%(product)s\n生产移动单位：%(move_uom)s\n问题：%(reason)s') % {
                    'product': product.display_name,
                    'move_uom': move_uom.display_name,
                    'reason': fix_reason,
                }
            )
        
        return False
    
    def do_pass(self):
        """
        质检通过时执行组件扫码确认
        """
        # 在调用父类方法之前，确保所有可能被 register_consumed_materials 使用的产品都有正确的 UOM
        if self.production_id:
            # 校验 point_id.component_id 的 UOM
            if self.point_id and self.point_id.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.point_id.component_id.id
                )
                if component_move and component_move[0].product_uom:
                    self._ensure_product_uom_consistency(
                        self.point_id.component_id,
                        component_move[0].product_uom
                    )
            
            # 校验 self.component_id 的 UOM
            if self.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.component_id.id
                )
                if component_move and component_move[0].product_uom:
                    self._ensure_product_uom_consistency(
                        self.component_id,
                        component_move[0].product_uom
                    )
                    
                    # 确保 self.move_id 被正确设置
                    move = component_move[0]
                    if not self.move_id or self.move_id.id != move.id:
                        self.move_id = move.id
                        self.invalidate_recordset(['move_id'])
                    
                    self._ensure_positive_uom_rounding(
                        move.product_uom,
                        _('质检组件移动：%s') % move.display_name
                    )
        
        # 如果是组件扫码确认类型的质检，执行验证
        if self.test_type == 'component_scan_verify':
            # 如果质检点配置了待登记组件，则自动使用配置的组件
            if self.point_id and self.point_id.component_id and not self.selected_component_id:
                self.selected_component_id = self.point_id.component_id
            
            # 检查是否已选择待登记组件
            if not self.selected_component_id:
                raise UserError(_('请先选择待登记的组件！'))

            if self.selected_move_line_id:
                if self.selected_move_line_id.move_id not in self.production_id.move_raw_ids:
                    raise UserError(_('选择的待登记组件移动行不属于当前生产订单！'))
                if self.selected_move_line_id.product_id != self.selected_component_id:
                    raise UserError(_('选择的待登记组件移动行与待登记组件不一致！'))
                if not self.move_line_id:
                    self.move_line_id = self.selected_move_line_id
                if not self.move_id:
                    self.move_id = self.selected_move_line_id.move_id
                if not self.component_id:
                    self.component_id = self.selected_component_id

            # 检查是否已经验证过
            if not self.scanned_component_id:
                raise UserError(_('请先扫码确认组件！'))
            
            # **关键修复**：重新验证扫码的组件是否匹配选中的待登记组件
            # 防止用户扫描错误组件后，通过其他方式绕过验证
            # 必须同时满足两个条件：1. 组件ID匹配 2. 验证结果必须是 matched
            if self.scanned_component_id.id != self.selected_component_id.id:
                # 组件不匹配，强制设置验证结果为失败
                self.component_verification_result = 'mismatched'
                self.component_verification_message = _(
                    '组件不匹配！\n'
                    '选择的待登记组件：%s (%s)\n'
                    '扫码的组件：%s (%s)\n'
                    '无法通过质检！'
                ) % (
                    self.selected_component_id.name,
                    self.selected_component_id.default_code or '',
                    self.scanned_component_id.name,
                    self.scanned_component_id.default_code or ''
                )
                _logger.warning(
                    _("[组件扫码确认] do_pass 验证失败: 质检ID=%s, 选择的组件=%s(ID:%s), 扫码的组件=%s(ID:%s)"),
                    self.id,
                    self.selected_component_id.name, self.selected_component_id.id,
                    self.scanned_component_id.name, self.scanned_component_id.id
                )
                raise UserError(_('组件验证失败，无法通过质检！\n%s') % self.component_verification_message)
            
            # **关键修复**：如果待登记组件有批次号，还需要验证批次号是否匹配
            if self.selected_lot_id:
                if not self.scanned_lot_id:
                    raise UserError(_('批次号不匹配！\n待登记组件批次号：%s\n扫码的组件没有批次号或批次号不匹配') % self.selected_lot_id.name)
                
                if self.scanned_lot_id.id != self.selected_lot_id.id:
                    raise UserError(_('批次号不匹配！\n待登记组件批次号：%s\n扫码的组件批次号：%s\n请确保扫码的批次号与待登记组件的批次号一致') % (
                        self.selected_lot_id.name,
                        self.scanned_lot_id.name
                    ))
            
            # **关键修复**：必须通过 verify_component 方法验证，不能直接通过 do_pass 绕过
            # 如果验证结果不是 matched，即使组件ID匹配，也不允许通过
            # 这防止了用户扫描错误组件后，通过修改 scanned_component_id 来绕过验证
            if self.component_verification_result != 'matched':
                # 如果验证结果不是 matched，说明没有通过 verify_component 验证
                # 即使组件ID匹配，也不允许通过（防止绕过验证）
                _logger.warning(
                    _("[组件扫码确认] do_pass 验证失败: 质检ID=%s, 验证结果=%s, 选择的组件=%s(ID:%s), 扫码的组件=%s(ID:%s)"),
                    self.id,
                    self.component_verification_result,
                    self.selected_component_id.name, self.selected_component_id.id,
                    self.scanned_component_id.name, self.scanned_component_id.id
                )
                raise UserError(_('组件验证失败，无法通过质检！\n%s\n\n请先通过扫码验证组件！') % (
                    self.component_verification_message or _('请确保扫码的组件匹配选中的待登记组件')
                ))
            
            # 确保组件的计量单位与生产订单移动行中的单位一致
            if self.production_id and self.selected_component_id:
                # 确保 Odoo 原生的 component_id 字段也被设置
                if not self.component_id:
                    self.component_id = self.selected_component_id.id
                
                # 查找生产订单中该组件的移动行
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.selected_component_id.id
                )
                
                if component_move and component_move[0].product_uom:
                    move = component_move[0]
                    component_product = self.selected_component_id
                    
                    self._ensure_product_uom_consistency(component_product, move.product_uom)
                    
                    # 确保 component_id 也使用更新后的产品
                    if self.component_id != component_product.id:
                        self.component_id = component_product.id
            
            # 在调用父类方法之前，强制刷新环境并重新加载所有相关记录
            if self.component_id:
                self.component_id.invalidate_recordset(['uom_id'])
                if self.component_id.product_tmpl_id:
                    self.component_id.product_tmpl_id.invalidate_recordset(['uom_id'])
                
                # 重新从数据库加载
                fresh_component = self.env['product.product'].browse(self.component_id.id)
                fresh_template = fresh_component.product_tmpl_id
                
                if fresh_template and not fresh_template.uom_id:
                    self._raise_uom_configuration_error(
                        _('产品模板缺少主计量单位，无法通过质检。'),
                        _('产品：%(product)s\n产品模板：%(template)s') % {
                            'product': fresh_component.display_name,
                            'template': fresh_template.display_name,
                        }
                    )
                
                # 确保 self.component_id 使用最新的记录
                self.component_id = fresh_component
        
        # 调用父类方法执行质检通过
        res = super(QualityCheck, self).do_pass()
        
        return res
    
    def _create_extra_move_lines(self):
        """
        覆盖父类方法，确保 move_id.product_uom 正确
        """
        if self.production_id and self.component_id and self.move_id:
            if not self.move_id.product_uom:
                self._raise_uom_configuration_error(
                    _('库存移动缺少计量单位，无法创建额外移动行。'),
                    _('库存移动：%(move)s\n组件：%(component)s') % {
                        'move': self.move_id.display_name,
                        'component': self.component_id.display_name,
                    }
                )
            
            self._ensure_positive_uom_rounding(
                self.move_id.product_uom,
                _('额外移动行：%s') % self.move_id.display_name
            )
        
        return super(QualityCheck, self)._create_extra_move_lines()
    
    @api.depends('selected_component_id', 'production_id', 'move_line_id', 'selected_move_line_id', 'selected_move_line_id.lot_id')
    def _compute_selected_lot_id(self):
        """计算待登记组件对应的批次号
        
        从生产订单的移动行中获取待登记组件对应的批次号
        """
        for record in self:
            if not record.selected_component_id or not record.production_id:
                record.selected_lot_id = False
                continue
            
            # 优先从用户明确选择的待登记移动行获取批次号
            if record.selected_move_line_id:
                record.selected_lot_id = record.selected_move_line_id.lot_id
                continue

            # 再从原生 move_line_id 获取批次号（如果已分配）
            if record.move_line_id and record.move_line_id.lot_id:
                record.selected_lot_id = record.move_line_id.lot_id
                continue
            
            # 从生产订单的移动行中查找待登记组件对应的批次号
            # 查找该组件的移动行，优先查找已分配且有批次号的移动行
            component_moves = record.production_id.move_raw_ids.filtered(
                lambda m: m.product_id.id == record.selected_component_id.id
            )
            
            if component_moves:
                # 查找有批次号的移动行
                for move in component_moves:
                    move_lines = move.move_line_ids.filtered(lambda ml: ml.lot_id)
                    if move_lines:
                        # 如果有多个移动行，优先选择与当前质检记录关联的移动行
                        if record.move_line_id and record.move_line_id in move_lines:
                            record.selected_lot_id = record.move_line_id.lot_id
                        else:
                            # 选择第一个有批次号的移动行
                            record.selected_lot_id = move_lines[0].lot_id
                        break
                else:
                    record.selected_lot_id = False
            else:
                record.selected_lot_id = False
    
    def get_configured_component(self):
        """
        获取质检点配置的待登记组件
        
        此方法由前端调用，用于获取质检点配置的组件
        参考 register_consumed_materials 的实现，使用 component_id 字段
        
        :return: 组件产品信息字典，如果没有配置则返回 False
        """
        if not self.point_id:
            return False
        
        # 使用 Odoo 原生的 component_id 字段
        if self.point_id.component_id:
            return {
                'id': self.point_id.component_id.id,
                'name': self.point_id.component_id.name,
                'code': self.point_id.component_id.default_code or '',
            }
        
        return False

    def get_component_scan_candidates(self):
        """Return selectable component candidates at move-line/lot granularity."""
        self.ensure_one()
        if not self.production_id:
            return []

        candidates = []
        for move in self.production_id.move_raw_ids.filtered(
            lambda m: m.state in ('assigned', 'partially_available', 'done')
            and m.product_uom_qty > m.quantity
        ):
            move_lines = move.move_line_ids
            if move_lines:
                for move_line in move_lines:
                    qty = move_line.quantity or 0.0
                    candidates.append({
                        'key': f'ml-{move_line.id}',
                        'move_id': move.id,
                        'move_line_id': move_line.id,
                        'id': move.product_id.id,
                        'name': move.product_id.name,
                        'code': move.product_id.default_code or '',
                        'lot_id': move_line.lot_id.id or False,
                        'lot_name': move_line.lot_id.name or move_line.lot_name or '',
                        'quantity': qty or (move.product_uom_qty - move.quantity),
                        'plannedQty': move.product_uom_qty,
                        'consumedQty': move.quantity,
                    })
            else:
                candidates.append({
                    'key': f'move-{move.id}',
                    'move_id': move.id,
                    'move_line_id': False,
                    'id': move.product_id.id,
                    'name': move.product_id.name,
                    'code': move.product_id.default_code or '',
                    'lot_id': False,
                    'lot_name': '',
                    'quantity': move.product_uom_qty - move.quantity,
                    'plannedQty': move.product_uom_qty,
                    'consumedQty': move.quantity,
                })
        return candidates

    def verify_component(self, scanned_component_id=None, scanned_lot_id=None):
        """
        验证扫码的组件是否匹配选中的待登记组件
        
        此方法由前端调用，用于实时验证
        验证逻辑：
        1. 检查是否已选择待登记组件
        2. 验证扫码的组件是否匹配选中的待登记组件（产品ID和批次号）
        3. 记录验证结果
        
        :param scanned_component_id: 扫码的组件ID（前端传递）
        :param scanned_lot_id: 扫码的批次号ID（前端传递）
        """
        if not self.production_id:
            raise UserError(_('无法获取生产订单信息，请确保质检点关联了生产订单！'))
        
        if not self.selected_component_id:
            raise UserError(_('请先选择待登记的组件！'))
        
        # 如果前端传递了扫码的组件ID，先设置到记录中
        if scanned_component_id:
            self.scanned_component_id = scanned_component_id
        
        # 前端每次验证都传递本次扫码批次；无批次时必须清空旧值，避免复用上次扫码批次。
        if scanned_lot_id is not None:
            self.scanned_lot_id = scanned_lot_id or False
        
        if not self.scanned_component_id:
            raise UserError(_('请先扫码确认组件！'))
        
        production = self.production_id
        selected_product = self.selected_component_id
        scanned_product = self.scanned_component_id
        
        # **关键修复**：验证产品ID是否匹配
        if scanned_product.id != selected_product.id:
            # 组件不匹配
            self.component_verification_result = 'mismatched'
            self.component_verification_message = _(
                '组件不匹配！\n'
                '选择的待登记组件：%s (%s)\n'
                '扫码的组件：%s (%s)'
            ) % (
                selected_product.name,
                selected_product.default_code or '',
                scanned_product.name,
                scanned_product.default_code or ''
            )
            
            # 记录日志（跳过邮件发送，避免未配置邮件时报错）
            try:
                self.with_context(mail_notrack=True).message_post(
                    body=_('组件验证失败<br/>选择的待登记组件：%s (%s)<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                        selected_product.name,
                        selected_product.default_code or '',
                        scanned_product.name,
                        scanned_product.default_code or '',
                        production.name
                    )
                )
            except Exception as e:
                # 如果 message_post 失败（例如未配置邮件），只记录日志，不抛出异常
                _logger.warning(
                    _("[组件扫码确认] 记录验证失败消息时出错（已跳过）: %s"),
                    str(e)
                )
            
            _logger.warning(
                _("[组件扫码确认] 验证失败: 质检ID=%s, 选择的组件=%s(ID:%s), 扫码的组件=%s(ID:%s), 生产订单=%s"),
                self.id, 
                selected_product.name, selected_product.id,
                scanned_product.name, scanned_product.id,
                production.name
            )
            
            return {'success': False, 'message': self.component_verification_message}
        
        # **关键修复**：如果待登记组件有批次号，还需要验证批次号是否匹配
        # 防止同一生产订单内不同批次的组件互相验证通过
        if self.selected_lot_id:
            # 如果待登记组件有批次号，扫码的批次号必须匹配
            if not self.scanned_lot_id:
                # 扫码时没有获取到批次号，验证失败
                self.component_verification_result = 'mismatched'
                self.component_verification_message = _(
                    '批次号不匹配！\n'
                    '待登记组件批次号：%s\n'
                    '扫码的组件没有批次号或批次号不匹配'
                ) % self.selected_lot_id.name
                _logger.warning(
                    _("[组件扫码确认] 批次号验证失败: 质检ID=%s, 待登记批次号=%s, 扫码批次号=无"),
                    self.id, self.selected_lot_id.name
                )
                return {'success': False, 'message': self.component_verification_message}
            
            if self.scanned_lot_id.id != self.selected_lot_id.id:
                # 批次号不匹配
                self.component_verification_result = 'mismatched'
                self.component_verification_message = _(
                    '批次号不匹配！\n'
                    '待登记组件批次号：%s\n'
                    '扫码的组件批次号：%s\n'
                    '请确保扫码的批次号与待登记组件的批次号一致'
                ) % (
                    self.selected_lot_id.name,
                    self.scanned_lot_id.name
                )
                _logger.warning(
                    _("[组件扫码确认] 批次号验证失败: 质检ID=%s, 待登记批次号=%s(ID:%s), 扫码批次号=%s(ID:%s)"),
                    self.id,
                    self.selected_lot_id.name, self.selected_lot_id.id,
                    self.scanned_lot_id.name, self.scanned_lot_id.id
                )
                return {'success': False, 'message': self.component_verification_message}
        
        # 组件匹配（产品ID匹配，如果有批次号要求，批次号也匹配）
        self.component_verification_result = 'matched'
        self.component_verification_message = _(
            '组件验证成功！\n'
            '选择的待登记组件：%s (%s)\n'
            '扫码的组件：%s (%s)\n'
            '匹配成功！'
        ) % (
            selected_product.name,
            selected_product.default_code or '',
            scanned_product.name,
            scanned_product.default_code or ''
        )
        
        # 记录日志（跳过邮件发送，避免未配置邮件时报错）
        try:
            self.with_context(mail_notrack=True).message_post(
                body=_('组件验证成功<br/>选择的待登记组件：%s (%s)<br/>扫码的组件：%s (%s)<br/>生产订单：%s') % (
                    selected_product.name,
                    selected_product.default_code or '',
                    scanned_product.name,
                    scanned_product.default_code or '',
                    production.name
                )
            )
        except Exception as e:
            # 如果 message_post 失败（例如未配置邮件），只记录日志，不抛出异常
            _logger.warning(
                _("[组件扫码确认] 记录验证成功消息时出错（已跳过）: %s"),
                str(e)
            )
        
        _logger.info(
            _("[组件扫码确认] 验证成功: 质检ID=%s, 选择的组件=%s, 扫码的组件=%s, 生产订单=%s"),
            self.id, selected_product.name, scanned_product.name, production.name
        )
        
        # 确保 component_id 字段被设置（register_consumed_materials 需要）
        if not self.component_id:
            self.component_id = selected_product.id

        return {
            'success': True,
            'message': self.component_verification_message,
            'auto_passed': False,
        }
    
    def action_scan_component(self):
        """
        扫码组件（前端调用）
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'component_scan_verify',
            'context': {
                'quality_check_id': self.id,
            }
        }
    
    def _update_component_quantity(self):
        """
        更新组件数量
        
        当产品或移动的计量单位配置异常时阻断，不在质检流程中自动修改主数据。
        """
        self.ensure_one()
        
        if hasattr(self, 'move_id') and self.move_id and self.move_id.product_uom:
            self._ensure_positive_uom_rounding(
                self.move_id.product_uom,
                _('质检库存移动：%s') % self.move_id.display_name
            )
        
        if self.component_id and self.component_id.uom_id:
            self._ensure_positive_uom_rounding(
                self.component_id.uom_id,
                _('组件产品：%s') % self.component_id.display_name
            )
        
        # 如果 move_id 不存在，尝试从生产订单移动行获取
        if not hasattr(self, 'move_id') or not self.move_id:
            if self.production_id and self.component_id:
                component_move = self.production_id.move_raw_ids.filtered(
                    lambda m: m.product_id.id == self.component_id.id
                )
                if component_move:
                    self.move_id = component_move[0].id
                    self.invalidate_recordset(['move_id'])
        
        # 如果仍然没有 move_id，调用父类方法
        if not self.move_id:
            return super(QualityCheck, self)._update_component_quantity()
        
        # 重新加载 move，确保获取最新值
        move = self.env['stock.move'].browse(self.move_id.id)
        
        if not move.product_uom:
            self._raise_uom_configuration_error(
                _('库存移动缺少计量单位，无法更新组件数量。'),
                _('库存移动：%(move)s\n组件：%(component)s') % {
                    'move': move.display_name,
                    'component': self.component_id.display_name if self.component_id else '-',
                }
            )
        
        move_uom = move.product_uom
        self._ensure_positive_uom_rounding(
            move_uom,
            _('组件数量更新：%s') % move.display_name
        )
        
        # 确保 rounding 有效
        rounding = move_uom.rounding
        
        # 在调用父类方法之前，确保 self.move_id 使用最新的 move
        self.move_id = move
        self.invalidate_recordset(['move_id'])
        self.env['stock.move'].flush_model(['product_uom'])
        self.env['uom.uom'].flush_model(['rounding'])
        
        # 如果是序列号组件，使用父类方法
        if self.component_tracking == 'serial':
            return super(QualityCheck, self)._update_component_quantity()
        
        # 计算新的数量（使用父类方法中的逻辑）
        try:
            new_qty = self._prepare_component_quantity(move, self.workorder_id.qty_producing)
        except Exception as e:
            _logger.error(_("[组件数量更新] 计算新数量失败: %s"), str(e))
            return super(QualityCheck, self)._update_component_quantity()
        
        # 使用向下取整而不是四舍五入，避免 0.255 被四舍五入到 0.26
        # 先向下取整到 rounding 的倍数，保留原始精度
        if rounding > 0:
            # 向下取整：将数量向下取整到 rounding 的倍数
            qty_todo = math.floor(new_qty / rounding) * rounding
        else:
            qty_todo = new_qty
        
        # 如果 move 已拣选且质检状态不是通过，需要减去已拣选的数量
        if (move.picked and self.quality_state != 'pass'):
            qty_todo = qty_todo - move.quantity
        
        # 如果有 move_line_id 和 lot_id，取最小值
        if self.move_line_id and self.move_line_id.lot_id:
            qty_todo = min(self.move_line_id.quantity, qty_todo)
        
        # 设置已完成数量（Odoo 18 使用 quantity，旧版本使用 qty_done）
        if 'quantity' in self._fields:
            self.quantity = qty_todo
        elif 'qty_done' in self._fields:
            self.qty_done = qty_todo
        
        return True


class MrpProductionWorkcenterLine(models.Model):
    _inherit = 'mrp.workorder'

    def _sync_matched_component_scan_checks(self):
        """Pass component scan checks that were verified but not persisted as done."""
        matched_checks = self.mapped('check_ids').filtered(
            lambda check: check.quality_state == 'none'
            and check.test_type_id.technical_name == 'component_scan_verify'
            and check.component_verification_result == 'matched'
        )
        for check in matched_checks:
            _logger.info(
                _("[组件扫码确认] 同步已匹配质检为通过: 质检ID=%s, 工单=%s"),
                check.id,
                check.workorder_id.name,
            )
            check.do_pass()

    def verify_quality_checks(self):
        self._sync_matched_component_scan_checks()
        return super().verify_quality_checks()

    def pre_record_production(self):
        self._sync_matched_component_scan_checks()
        return super().pre_record_production()

    def get_summary_data(self):
        return super().get_summary_data()
