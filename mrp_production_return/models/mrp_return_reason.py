# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class MrpReturnReason(models.Model):
    _name = 'mrp.return.reason'
    _description = '制造订单返回原因'
    _order = 'sequence, name'

    name = fields.Char(
        string='原因名称',
        required=True,
        translate=True
    )
    code = fields.Char(
        string='代码',
        required=True,
        help='用于系统识别的唯一代码'
    )
    description = fields.Text(
        string='描述',
        translate=True,
        help='详细说明此原因'
    )
    sequence = fields.Integer(
        string='排序',
        default=10,
        help='用于排序的序号'
    )
    active = fields.Boolean(
        string='启用',
        default=True,
        help='是否启用此原因'
    )
    return_strategy = fields.Selection([
        ('before', '返回至生产前'),
        ('after', '返回至生产后'),
        ('defective', '返回至不良品仓'),
        ('scrap', '报废处理'),
    ], string='适用策略', help='此原因适用的返回策略')
    
    # 统计信息
    usage_count = fields.Integer(
        string='使用次数',
        compute='_compute_usage_count',
        help='此原因被使用的次数'
    )
    
    @api.depends('name')
    def _compute_usage_count(self):
        """计算使用次数"""
        for record in self:
            record.usage_count = self.env['mrp.production.return.history'].search_count([
                ('return_reason_id', '=', record.id)
            ])

    _sql_constraints = [
        ('code_uniq', 'unique (code)', '代码必须唯一！'),
    ]

    @api.model
    def get_default_reasons(self):
        """获取默认原因"""
        return [
            {
                'name': '质量不合格',
                'code': 'QUALITY_ISSUE',
                'description': '产品不符合质量标准',
                'return_strategy': 'defective',
            },
            {
                'name': '生产过剩',
                'code': 'OVER_PRODUCTION',
                'description': '生产数量超过需求',
                'return_strategy': 'defective',
            },
            {
                'name': '设备故障',
                'code': 'EQUIPMENT_FAILURE',
                'description': '生产设备出现故障',
                'return_strategy': 'defective',
            },
            {
                'name': '材料问题',
                'code': 'MATERIAL_ISSUE',
                'description': '原材料存在问题',
                'return_strategy': 'scrap',
            },
            {
                'name': '工艺调整',
                'code': 'PROCESS_ADJUSTMENT',
                'description': '生产工艺需要调整',
                'return_strategy': 'before',
            },
        ]
