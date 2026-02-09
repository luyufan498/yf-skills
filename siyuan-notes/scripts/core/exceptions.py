#!/usr/bin/env python3
"""
思源笔记自定义异常
"""


class SiyuanError(Exception):
    """思源笔记基础异常"""
    pass


class AuthenticationError(SiyuanError):
    """鉴权失败"""
    pass


class NotebookNotFoundError(SiyuanError):
    """笔记本不存在"""
    pass


class DocumentNotFoundError(SiyuanError):
    """文档不存在"""
    pass


class BlockNotFoundError(SiyuanError):
    """块不存在"""
    pass


class APIError(SiyuanError):
    """API 调用失败"""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"API error (code {code}): {msg}")


class ConfigError(SiyuanError):
    """配置错误"""
    pass


class ValidationError(SiyuanError):
    """参数验证失败"""
    pass
