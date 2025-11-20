# -*- coding: utf-8 -*-
"""
异常处理优化示例

展示如何将宽泛的 except Exception 改为具体的异常类型
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class ExceptionHandlingExamples:
    """异常处理优化示例"""
    
    # ========================================
    # 示例1：属性访问异常
    # ========================================
    
    def example_1_before(self):
        """❌ 优化前：捕获所有异常"""
        try:
            if self._origin.id:
                is_saved_record_by_origin = True
                original_record_id = self._origin.id
        except Exception:
            pass
    
    def example_1_after(self):
        """✅ 优化后：捕获具体异常"""
        try:
            if self._origin.id:
                is_saved_record_by_origin = True
                original_record_id = self._origin.id
        except AttributeError:
            # _origin 不存在或没有 id 属性
            pass
        except (TypeError, ValueError):
            # id 类型错误或值错误
            _logger.debug("无法获取原始记录ID")
            pass
    
    # ========================================
    # 示例2：数据验证异常
    # ========================================
    
    def example_2_before(self):
        """❌ 优化前：隐藏所有错误"""
        try:
            # 验证批次号
            self._validate_lot_name()
        except Exception as e:
            _logger.error(f"验证批次号时发生错误: {str(e)}")
            # 继续执行，可能导致数据不一致
    
    def example_2_after(self):
        """✅ 优化后：区分不同类型的错误"""
        try:
            # 验证批次号
            self._validate_lot_name()
        except ValidationError:
            # 验证错误应该抛出，让用户知道
            raise
        except UserError:
            # 用户错误也应该抛出
            raise
        except ValueError as e:
            # 数值错误，记录并抛出用户友好的错误
            _logger.error("批次号格式错误: %s", str(e), exc_info=True)
            raise UserError(_('批次号格式错误：%s') % str(e))
        except Exception as e:
            # 未预期的错误，记录详细信息并抛出
            _logger.error("验证批次号时发生未预期的错误: %s", str(e), exc_info=True)
            raise
    
    # ========================================
    # 示例3：数据库操作异常
    # ========================================
    
    def example_3_before(self):
        """❌ 优化前：忽略数据库错误"""
        try:
            record = self.browse(numeric_id)
            if record.exists():
                return record
        except Exception as e:
            _logger.error(f"查询记录时出错: {str(e)}")
            return None
    
    def example_3_after(self):
        """✅ 优化后：正确处理数据库错误"""
        try:
            record = self.browse(numeric_id)
            if record.exists():
                return record
        except (ValueError, TypeError) as e:
            # ID 类型错误
            _logger.warning("无效的记录ID: %s", numeric_id)
            return None
        except Exception as e:
            # 数据库错误应该抛出，不应该隐藏
            _logger.error("查询记录时发生数据库错误: %s", str(e), exc_info=True)
            raise
    
    # ========================================
    # 示例4：可选操作异常
    # ========================================
    
    def example_4_before(self):
        """❌ 优化前：捕获所有异常"""
        try:
            # 尝试获取可选信息
            optional_info = self._get_optional_info()
        except Exception:
            pass
    
    def example_4_after(self):
        """✅ 优化后：明确哪些异常是预期的"""
        try:
            # 尝试获取可选信息
            optional_info = self._get_optional_info()
        except (AttributeError, KeyError):
            # 预期的异常：属性或键不存在
            optional_info = None
        except Exception as e:
            # 非预期的异常，记录日志
            _logger.warning("获取可选信息时发生非预期错误: %s", str(e))
            optional_info = None
    
    # ========================================
    # 示例5：错误恢复异常
    # ========================================
    
    def example_5_before(self):
        """❌ 优化前：错误恢复逻辑不清晰"""
        try:
            # 业务逻辑
            self._process_data()
        except Exception as e:
            _logger.error(f"处理数据时出错: {str(e)}")
            # 检查错误消息来决定如何处理
            error_msg = str(e).lower()
            if 'duplicate' in error_msg:
                # 处理重复错误
                self._handle_duplicate()
    
    def example_5_after(self):
        """✅ 优化后：明确的错误恢复逻辑"""
        from psycopg2 import IntegrityError
        
        try:
            # 业务逻辑
            self._process_data()
        except IntegrityError as e:
            # 数据库完整性错误（如重复键）
            _logger.warning("数据完整性错误: %s", str(e))
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                self._handle_duplicate()
            else:
                raise UserError(_('数据保存失败：%s') % str(e))
        except ValidationError:
            # 验证错误应该抛出
            raise
        except Exception as e:
            # 其他错误
            _logger.error("处理数据时发生错误: %s", str(e), exc_info=True)
            raise


# ========================================
# 常用异常类型参考
# ========================================

"""
Odoo 常用异常类型：

1. ValidationError - 数据验证错误
   - 用于：字段验证、约束验证
   - 应该：抛出，让用户看到

2. UserError - 用户操作错误
   - 用于：业务逻辑错误、权限错误
   - 应该：抛出，显示友好的错误消息

3. AccessError - 访问权限错误
   - 用于：权限检查失败
   - 应该：抛出

Python 常用异常类型：

1. AttributeError - 属性不存在
   - 场景：访问对象不存在的属性
   - 处理：通常可以安全忽略或提供默认值

2. KeyError - 键不存在
   - 场景：访问字典不存在的键
   - 处理：通常可以安全忽略或提供默认值

3. ValueError - 值错误
   - 场景：参数值不正确
   - 处理：记录日志并抛出用户友好的错误

4. TypeError - 类型错误
   - 场景：参数类型不正确
   - 处理：记录日志并抛出用户友好的错误

5. IndexError - 索引错误
   - 场景：列表索引超出范围
   - 处理：检查列表长度或提供默认值

数据库相关异常（psycopg2）：

1. IntegrityError - 数据完整性错误
   - 场景：违反唯一约束、外键约束等
   - 处理：提供友好的错误消息

2. OperationalError - 操作错误
   - 场景：数据库连接问题、查询超时等
   - 处理：记录日志并抛出

异常处理最佳实践：

1. ✅ 捕获具体的异常类型，而不是 Exception
2. ✅ ValidationError 和 UserError 应该抛出，不要捕获
3. ✅ 使用 exc_info=True 记录完整的堆栈信息
4. ✅ 为用户提供友好的错误消息
5. ✅ 不要隐藏关键错误
6. ❌ 不要使用空的 except: pass
7. ❌ 不要捕获 Exception 然后什么都不做
8. ❌ 不要通过检查错误消息字符串来判断错误类型
"""
