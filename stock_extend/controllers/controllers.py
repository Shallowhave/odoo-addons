# -*- coding: utf-8 -*-
# from odoo import http


# class StockExtend(http.Controller):
#     @http.route('/stock_extend/stock_extend', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/stock_extend/stock_extend/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('stock_extend.listing', {
#             'root': '/stock_extend/stock_extend',
#             'objects': http.request.env['stock_extend.stock_extend'].search([]),
#         })

#     @http.route('/stock_extend/stock_extend/objects/<model("stock_extend.stock_extend"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('stock_extend.object', {
#             'object': obj
#         })

