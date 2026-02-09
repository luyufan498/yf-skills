#!/usr/bin/env python3
"""
查询命令处理器
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from core.client import SiyuanClient
from utils.format import OutputFormatter
from core.exceptions import SiyuanError


class QueryCommand:
    """查询命令处理器"""

    def __init__(self):
        self.client = SiyuanClient()
        self.formatter = OutputFormatter()

    def sql(self, stmt: str, format: str = "text"):
        """
        执行 SQL 查询
        
        Args:
            stmt: SQL 语句
            format: 输出格式
        """
        try:
            results = self.client.query_sql(stmt)
            
            if format == "json":
                print(self.formatter.json(results))
            else:
                print(f"\n🔍 SQL 查询结果 ({len(results)} 条)")
                print("=" * 60)
                for i, row in enumerate(results, 1):
                    print(f"\n[{i}]")
                    for k, v in row.items():
                        print(f"  {k}: {v}")
                        
        except SiyuanError as e:
            print(f"✗ 查询失败: {e}")

    def search(self, keyword: str, notebook: str = None, 
              type: str = None, limit: int = 10):
        """
        搜索包含关键词的块
        
        Args:
            keyword: 关键词
            notebook: 笔记本 ID（可选）
            type: 块类型（可选）
            limit: 返回数量限制
        """
        try:
            conditions = [f"content LIKE '%{keyword}%'"]
            if notebook:
                nb_client = __import__('modules.notebook.client', fromlist=['NotebookClient'])
                nb = nb_client.NotebookClient().find_notebook_by_name(notebook)
                conditions.append(f"box = '{nb.id}'")
            if type:
                conditions.append(f"type = '{type}'")
            
            where_clause = " AND ".join(conditions)
            stmt = f"SELECT * FROM blocks WHERE {where_clause} LIMIT {limit}"
            
            results = self.client.query_sql(stmt)
            
            print(f"\n🔍 搜索结果: '{keyword}' ({len(results)} 条)")
            print("=" * 60)
            
            for i, block in enumerate(results, 1):
                block_type = block.get('type', 'unknown')
                content = block.get('content', '')[:80]
                print(f"\n{i}. [{block_type}] {content}...")
                print(f"   ID: {block.get('id', '')}")
                
        except SiyuanError as e:
            print(f"✗ 搜索失败: {e}")

    def attr(self, key: str, value: str, notebook: str = None):
        """
        按属性查询块
        
        Args:
            key: 属性键
            value: 属性值
            notebook: 笔记本名称（可选）
        """
        try:
            conditions = [f"`{key}` = '{value}'"]
            if notebook:
                nb_client = __import__('modules.notebook.client', fromlist=['NotebookClient'])
                nb = nb_client.NotebookClient().find_notebook_by_name(notebook)
                conditions.append(f"box = '{nb.id}'")
            
            where_clause = " AND ".join(conditions)
            stmt = f"SELECT * FROM blocks WHERE {where_clause}"
            
            results = self.client.query_sql(stmt)
            
            print(f"\n🔍 属性查询: {key}={value} ({len(results)} 条)")
            print("=" * 60)
            
            for i, block in enumerate(results, 1):
                content = block.get('content', '')[:80]
                print(f"\n{i}. {content}...")
                print(f"   ID: {block.get('id', '')}")
                
        except SiyuanError as e:
            print(f"✗ 查询失败: {e}")

    def recent(self, limit: int = 20):
        """
        获取最近更新的块
        
        Args:
            limit: 返回数量限制
        """
        try:
            stmt = f"""
            SELECT * FROM blocks
            ORDER BY updated DESC
            LIMIT {limit}
            """
            
            results = self.client.query_sql(stmt)
            
            print(f"\n🕐 最近更新 ({len(results)} 条)")
            print("=" * 60)
            
            for i, block in enumerate(results, 1):
                block_type = block.get('type', 'unknown')
                content = block.get('content', '')[:60]
                updated = block.get('updated', '')[:19]
                print(f"{i}. [{block_type}] {content}...")
                print(f"   更新: {updated}")
                
        except SiyuanError as e:
            print(f"✗ 查询失败: {e}")
