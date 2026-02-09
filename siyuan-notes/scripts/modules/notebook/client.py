#!/usr/bin/env python3
"""
笔记本操作客户端
"""

from typing import List, Dict, Any
import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from core.client import SiyuanClient
from core.models import Notebook
from core.exceptions import NotebookNotFoundError


class NotebookClient(SiyuanClient):
    """笔记本操作客户端"""

    def list_notebooks(self) -> List[Notebook]:
        """
        获取所有笔记本列表
        
        Returns:
            笔记本列表
        """
        data = self._call_api("/api/notebook/lsNotebooks")
        notebooks = data.get("notebooks", [])
        return [Notebook.from_dict(nb) for nb in notebooks]

    def get_notebook(self, notebook_id: str) -> Notebook:
        """
        获取单个笔记本
        
        Args:
            notebook_id: 笔记本 ID
            
        Returns:
            笔记本对象
            
        Raises:
            NotebookNotFoundError: 笔记本不存在
        """
        notebooks = self.list_notebooks()
        for nb in notebooks:
            if nb.id == notebook_id:
                return nb
        raise NotebookNotFoundError(f"笔记本不存在: {notebook_id}")

    def find_notebook_by_name(self, name: str) -> Notebook:
        """
        根据名称查找笔记本（支持部分匹配）
        
        Args:
            name: 笔记本名称
            
        Returns:
            笔记本对象
            
        Raises:
            NotebookNotFoundError: 笔记本不存在
        """
        notebooks = self.list_notebooks()
        for nb in notebooks:
            if name.lower() in nb.name.lower():
                return nb
        raise NotebookNotFoundError(f"未找到笔记本: {name}")

    def create_notebook(self, name: str) -> Notebook:
        """
        创建笔记本
        
        Args:
            name: 笔记本名称
            
        Returns:
            创建的笔记本对象
        """
        result = self._call_api("/api/notebook/createNotebook", {"name": name})
        return Notebook.from_dict(result.get("notebook", {}))

    def remove_notebook(self, notebook_id: str) -> None:
        """
        删除笔记本
        
        Args:
            notebook_id: 笔记本 ID
        """
        self._call_api("/api/notebook/removeNotebook", {"notebook": notebook_id})

    def open_notebook(self, notebook_id: str) -> None:
        """
        打开笔记本
        
        Args:
            notebook_id: 笔记本 ID
        """
        self._call_api("/api/notebook/openNotebook", {"notebook": notebook_id})

    def close_notebook(self, notebook_id: str) -> None:
        """
        关闭笔记本
        
        Args:
            notebook_id: 笔记本 ID
        """
        self._call_api("/api/notebook/closeNotebook", {"notebook": notebook_id})

    def rename_notebook(self, notebook_id: str, name: str) -> None:
        """
        重命名笔记本
        
        Args:
            notebook_id: 笔记本 ID
            name: 新名称
        """
        self._call_api(
            "/api/notebook/renameNotebook",
            {"notebook": notebook_id, "name": name}
        )

    def get_conf(self, notebook_id: str) -> Dict[str, Any]:
        """
        获取笔记本配置
        
        Args:
            notebook_id: 笔记本 ID
            
        Returns:
            配置字典
        """
        return self._call_api(
            "/api/notebook/getNotebookConf",
            {"notebook": notebook_id}
        )

    def set_conf(self, notebook_id: str, conf: Dict[str, Any]) -> None:
        """
        设置笔记本配置
        
        Args:
            notebook_id: 笔记本 ID
            conf: 配置数据
        """
        self._call_api(
            "/api/notebook/setNotebookConf",
            {"notebook": notebook_id, "conf": conf}
        )
