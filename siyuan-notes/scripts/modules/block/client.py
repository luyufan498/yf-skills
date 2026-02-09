#!/usr/bin/env python3
"""
块操作客户端
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from core.client import SiyuanClient
from core.models import Block
from core.exceptions import BlockNotFoundError
from typing import List, Dict, Any


class BlockClient(SiyuanClient):
    """块操作客户端"""

    def get_block(self, block_id: str) -> Block:
        """
        获取块信息
        
        Args:
            block_id: 块 ID
            
        Returns:
            块对象
        """
        # 通过 SQL 查询获取块信息
        stmt = f"SELECT * FROM blocks WHERE id = '{block_id}'"
        results = self.query_sql(stmt)
        
        if not results:
            raise BlockNotFoundError(f"块不存在: {block_id}")
        
        return Block.from_dict(results[0])

    def search_blocks(self, keyword: str, notebook_id: str = None, 
                     block_type: str = None, limit: int = 100) -> List[Block]:
        """
        搜索块
        
        Args:
            keyword: 关键词
            notebook_id: 笔记本 ID（可选）
            block_type: 块类型（可选）
            limit: 返回数量限制
            
        Returns:
            块列表
        """
        conditions = []
        if keyword:
            conditions.append(f"content LIKE '%{keyword}%'")
        if notebook_id:
            conditions.append(f"box = '{notebook_id}'")
        if block_type:
            conditions.append(f"type = '{block_type}'")
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        stmt = f"SELECT * FROM blocks WHERE {where_clause} LIMIT {limit}"
        
        results = self.query_sql(stmt)
        return [Block.from_dict(r) for r in results]

    def update_block_content(self, block_id: str, content: str) -> None:
        """
        更新块内容
        
        Args:
            block_id: 块 ID
            content: 新内容
        """
        self.update_block(block_id, content)

    def append_child_block(self, parent_id: str, content: str) -> str:
        """
        追加子块
        
        Args:
            parent_id: 父块 ID
            content: 内容
            
        Returns:
            新块的 ID
        """
        return self.append_block(content, parent_id)

    def prepend_child_block(self, parent_id: str, content: str) -> str:
        """
        前置子块
        
        Args:
            parent_id: 父块 ID
            content: 内容
            
        Returns:
            新块的 ID
        """
        return self.prepend_block(content, parent_id)

    def move_block_to(self, block_id: str, parent_id: str, 
                     previous_id: str = None) -> None:
        """
        移动块
        
        Args:
            block_id: 块 ID
            parent_id: 目标父块 ID
            previous_id: 前一个块 ID（可选）
        """
        self.move_block(block_id, previous_id or "", parent_id)

    def delete_block_by_id(self, block_id: str) -> None:
        """
        删除块
        
        Args:
            block_id: 块 ID
        """
        self.delete_block(block_id)

    def get_block_attributes(self, block_id: str) -> Dict[str, str]:
        """
        获取块属性
        
        Args:
            block_id: 块 ID
            
        Returns:
            属性字典
        """
        return self.get_block_attrs(block_id)

    def set_block_attribute(self, block_id: str, 
                           attrs: Dict[str, str]) -> None:
        """
        设置块属性
        
        Args:
            block_id: 块 ID
            attrs: 属性字典
        """
        self.set_block_attrs(block_id, attrs)
