# -*- coding: utf-8 -*-

import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class StockLot(models.Model):
    _inherit = 'stock.lot'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """重写搜索方法，添加日志以跟踪批次号查询"""
        # **关键修复**：无论 domain 是什么，都记录日志（用于调试）
        import sys
        import traceback
        
        # 检查是否是批次号名称搜索
        lot_name_domain = [d for d in domain if isinstance(d, (list, tuple)) and len(d) >= 2 and d[0] == 'name']
        lot_names = []
        if lot_name_domain:
            # 获取搜索的批次号名称
            lot_names = [d[2] for d in lot_name_domain if d[1] in ('=', 'in', 'ilike')]
        
        # **关键日志**：无论是否有 name 字段，都记录日志
        print("=" * 80, file=sys.stderr)
        print(f"[批次号搜索] stock.lot._search 被调用: domain={domain}", file=sys.stderr)
        print(f"[批次号搜索] lot_names={lot_names}, offset={offset}, limit={limit}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        
        _logger.error(
            f"[批次号搜索] ========== stock.lot._search 被调用 ========== "
            f"domain={domain}, lot_names={lot_names}, "
            f"offset={offset}, limit={limit}, order={order}"
        )
        
        # 获取调用栈信息（只取前3层，避免日志过长）
        try:
            caller_info = traceback.extract_stack()[-4:-1] if len(traceback.extract_stack()) > 4 else []
            caller_str = ' -> '.join([f"{c.filename.split('/')[-1]}:{c.lineno}" for c in caller_info[-2:]])
            _logger.error(f"[批次号搜索] 调用栈: {caller_str}")
            print(f"[批次号搜索] 调用栈: {caller_str}", file=sys.stderr)
        except:
            pass
        
        # 调用父类的搜索方法（这会调用 stock_barcode 模块的 _search 方法）
        result = super(StockLot, self)._search(domain, offset=offset, limit=limit, order=order)
        
        # 记录搜索结果（只记录输入参数，不访问结果，避免 SQL 错误）
        # 注意：_search 返回的是查询对象或记录 ID 列表，不应在此处访问记录内容
        _logger.error(
            f"[批次号搜索] 搜索完成: domain={domain}, "
            f"lot_names={lot_names}, offset={offset}, limit={limit}"
        )
        print(f"[批次号搜索] 搜索完成: domain={domain}, lot_names={lot_names}", file=sys.stderr)
        
        return result

    def read(self, fields=None, load='_classic_read'):
        """重写 read 方法，添加日志以跟踪批次号读取"""
        # 如果读取 name 字段，记录日志
        if fields is None or 'name' in fields:
            lot_names = [lot.name for lot in self if hasattr(lot, 'name') and lot.name]
            if lot_names:
                _logger.error(
                    f"[批次号读取] ========== stock.lot.read 被调用 ========== "
                    f"批次号列表={lot_names}, 记录数={len(self)}"
                )
        
        return super(StockLot, self).read(fields=fields, load=load)

