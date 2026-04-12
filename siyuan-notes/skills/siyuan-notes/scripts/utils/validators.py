#!/usr/bin/env python3
"""
参数验证器
"""

import re
from typing import Any, Optional


class Validators:
    """参数验证器"""

    # 思源笔记 ID 格式：14位时间戳 - 7位随机字符
    ID_PATTERN = re.compile(r'^\d{14}-[a-z0-9]{7}$')

    @staticmethod
    def validate_notebook_id(notebook_id: str) -> bool:
        """
        验证笔记本 ID 格式
        
        Args:
            notebook_id: 笔记本 ID
            
        Returns:
            是否有效
            
        Raises:
            ValidationError: ID 格式无效
        """
        if not Validators.ID_PATTERN.match(notebook_id):
            raise ValidationError(f"无效的笔记本 ID: {notebook_id}")
        return True

    @staticmethod
    def validate_block_id(block_id: str) -> bool:
        """
        验证块 ID 格式
        
        Args:
            block_id: 块 ID
            
        Returns:
            是否有效
            
        Raises:
            ValidationError: ID 格式无效
        """
        if not Validators.ID_PATTERN.match(block_id):
            raise ValidationError(f"无效的块 ID: {block_id}")
        return True

    @staticmethod
    def validate_path(path: str) -> bool:
        """
        验证路径格式
        
        Args:
            path: 路径字符串
            
        Returns:
            是否有效
            
        Raises:
            ValidationError: 路径格式无效
        """
        if not path.startswith('/'):
            raise ValidationError(f"路径必须以 / 开头: {path}")
        return True

    @staticmethod
    def validate_hpath(hpath: str) -> bool:
        """
        验证人类可读路径格式
        
        Args:
            hpath: 人类可读路径
            
        Returns:
            是否有效
        """
        if not hpath or hpath == '/':
            return True
        if not hpath.startswith('/'):
            raise ValidationError(f"路径必须以 / 开头: {hpath}")
        return True

    @staticmethod
    def validate_required(value: Any, name: str = "参数") -> bool:
        """
        验证必需参数
        
        Args:
            value: 参数值
            name: 参数名称
            
        Returns:
            是否有效
            
        Raises:
            ValidationError: 参数为空
        """
        if value is None or value == "":
            raise ValidationError(f"{name} 不能为空")
        return True
