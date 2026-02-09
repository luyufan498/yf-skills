#!/usr/bin/env python3
"""
思源笔记 API 基础客户端
"""

import os
import requests
from typing import Optional, Dict, Any, List

from .config import Config
from .exceptions import APIError, AuthenticationError


class SiyuanClient:
    """思源笔记 API 基础客户端"""

    def __init__(self, endpoint: Optional[str] = None, token: Optional[str] = None):
        self.endpoint = endpoint or Config.get_endpoint()
        self.token = token or Config.get_token()
        
        if not self.token:
            raise ValueError("API Token is required. Set SIYUAN_TOKEN environment variable")
        
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {self.token}",
            "Content-Type": "application/json"
        })

    def _call_api(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """调用思源笔记 API"""
        url = f"{self.endpoint}{endpoint}"
        try:
            if data:
                response = self.session.post(url, json=data)
            else:
                response = self.session.post(url)

            response.raise_for_status()
            result = response.json()

            if result.get("code") == 403:
                raise AuthenticationError("Authentication failed. Check your API token.")
            if result.get("code") != 0:
                raise APIError(result['code'], result.get('msg', 'Unknown error'))

            return result.get("data")
        except requests.RequestException as e:
            raise requests.RequestException(f"Failed to call API: {e}")

    # ==================== SQL 查询 ====================

    def query_sql(self, stmt: str) -> List[Dict[str, Any]]:
        """执行 SQL 查询"""
        return self._call_api("/api/query/sql", {"stmt": stmt})

    # ==================== 路径查询 ====================

    def get_hpath_by_id(self, doc_id: str) -> str:
        """根据 ID 获取人类可读路径"""
        return self._call_api(
            "/api/filetree/getHPathByID",
            {"id": doc_id}
        )

    def get_path_by_id(self, doc_id: str) -> Dict[str, str]:
        """根据 ID 获取存储路径"""
        return self._call_api(
            "/api/filetree/getPathByID",
            {"id": doc_id}
        )

    # ==================== 文档操作基础方法 ====================

    def create_doc_with_md(self, notebook_id: str, path: str, markdown: str) -> str:
        """通过 Markdown 创建文档"""
        return self._call_api(
            "/api/filetree/createDocWithMd",
            {
                "notebook": notebook_id,
                "path": path,
                "markdown": markdown
            }
        )

    def rename_doc_by_id(self, doc_id: str, title: str) -> None:
        """通过 ID 重命名文档"""
        self._call_api(
            "/api/filetree/renameDocByID",
            {"id": doc_id, "title": title}
        )

    def remove_doc_by_id(self, doc_id: str) -> None:
        """通过 ID 删除文档"""
        self._call_api("/api/filetree/removeDocByID", {"id": doc_id})

    def move_docs_by_id(self, from_ids: List[str], to_id: str) -> None:
        """通过 ID 移动文档"""
        self._call_api(
            "/api/filetree/moveDocsByID",
            {
                "fromIDs": from_ids,
                "toID": to_id
            }
        )

    # ==================== 块操作基础方法 ====================

    def update_block(self, block_id: str, data: str, data_type: str = "markdown") -> None:
        """更新块"""
        self._call_api(
            "/api/block/updateBlock",
            {
                "dataType": data_type,
                "data": data,
                "id": block_id
            }
        )

    def append_block(self, data: str, parent_id: str, data_type: str = "markdown") -> str:
        """在父块后插入子块"""
        result = self._call_api(
            "/api/block/appendBlock",
            {
                "dataType": data_type,
                "data": data,
                "parentID": parent_id
            }
        )
        return result[0]["doOperations"][0]["id"]

    def prepend_block(self, data: str, parent_id: str, data_type: str = "markdown") -> str:
        """在父块前插入子块"""
        result = self._call_api(
            "/api/block/prependBlock",
            {
                "dataType": data_type,
                "data": data,
                "parentID": parent_id
            }
        )
        return result[0]["doOperations"][0]["id"]

    def delete_block(self, block_id: str) -> None:
        """删除块"""
        self._call_api("/api/block/deleteBlock", {"id": block_id})

    def move_block(self, block_id: str, previous_id: str = "", parent_id: str = "") -> None:
        """移动块"""
        self._call_api(
            "/api/block/moveBlock",
            {
                "id": block_id,
                "previousID": previous_id,
                "parentID": parent_id
            }
        )

    def get_block_attrs(self, block_id: str) -> Dict[str, str]:
        """获取块属性"""
        return self._call_api(
            "/api/attr/getBlockAttrs",
            {"id": block_id}
        )

    def set_block_attrs(self, block_id: str, attrs: Dict[str, str]) -> None:
        """设置块属性"""
        self._call_api(
            "/api/attr/setBlockAttrs",
            {
                "id": block_id,
                "attrs": attrs
            }
        )

    # ==================== 导出功能 ====================

    def export_md_content(self, doc_id: str) -> Dict[str, str]:
        """导出文档的 Markdown 内容"""
        return self._call_api(
            "/api/export/exportMdContent",
            {"id": doc_id}
        )

    def export_resources(
        self,
        paths: List[str],
        name: Optional[str] = None
    ) -> Dict[str, str]:
        """导出文件与目录"""
        data = {"paths": paths}
        if name:
            data["name"] = name
        return self._call_api("/api/export/exportResources", data)
