#!/usr/bin/env python3
"""
文档操作客户端
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from core.client import SiyuanClient
from core.models import Document
from core.exceptions import DocumentNotFoundError
from typing import List, Dict, Any


class DocumentClient(SiyuanClient):
    """文档操作客户端"""

    def get_document(self, doc_id: str) -> Document:
        """
        获取文档详情
        
        Args:
            doc_id: 文档 ID
            
        Returns:
            文档对象
        """
        # 导出 Markdown 获取详细信息
        result = self.export_md_content(doc_id)
        return Document(
            id=doc_id,
            notebook_id="",
            title=result.get('hPath', '').split('/')[-1],
            hpath=result.get('hPath', ''),
            path="",
            created="",
            updated="",
            content=result.get('content', '')
        )

    def list_documents(self, notebook_id: str) -> List[Document]:
        """
        列出笔记本下的所有文档
        
        Args:
            notebook_id: 笔记本 ID
            
        Returns:
            文档列表
        """
        stmt = f"""
        SELECT * FROM blocks
        WHERE box = '{notebook_id}' AND type = 'd'
        ORDER BY created DESC
        """
        blocks = self.query_sql(stmt)
        return [Document.from_dict(b) for b in blocks]

    def create_document(self, notebook_id: str, path: str, 
                       content: str = "") -> str:
        """
        创建文档
        
        Args:
            notebook_id: 笔记本 ID
            path: 文档路径
            content: Markdown 内容
            
        Returns:
            文档 ID
        """
        return self.create_doc_with_md(notebook_id, path, content)

    def rename_document(self, doc_id: str, new_title: str) -> None:
        """
        重命名文档
        
        Args:
            doc_id: 文档 ID
            new_title: 新标题
        """
        self.rename_doc_by_id(doc_id, new_title)

    def move_document(self, doc_id: str, target_id: str) -> None:
        """
        移动文档
        
        Args:
            doc_id: 源文档 ID
            target_id: 目标父文档 ID 或笔记本 ID
        """
        self.move_docs_by_id([doc_id], target_id)

    def delete_document(self, doc_id: str) -> None:
        """
        删除文档

        Args:
            doc_id: 文档 ID
        """
        self.remove_doc_by_id(doc_id)

    def check_parent_path_exists(self, notebook_id: str, path: str) -> bool:
        """
        检查父文档路径是否存在

        Args:
            notebook_id: 笔记本 ID
            path: 父文档路径（如 "/" 或 "/父文档/子文档"）

        Returns:
            True if path exists, False otherwise
        """
        # 根路径总是存在
        if path == "/" or not path or path == ".":
            return True

        # 移除开头和结尾的斜杠，并规范化路径
        normalized_path = path.strip().strip("/")
        if not normalized_path:
            return True

        # 查询该路径下是否有文档
        # 如果父路径存在，那么应该能找到至少一个文档的 hpath 以该路径开头
        stmt = f"""
        SELECT * FROM blocks
        WHERE box = '{notebook_id}'
          AND type = 'd'
          AND hpath LIKE '/{normalized_path}%'
        LIMIT 1
        """
        try:
            results = self.query_sql(stmt)
            return len(results) > 0
        except:
            # 如果查询失败，保守地返回 True（允许创建）
            return False
