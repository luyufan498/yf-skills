#!/usr/bin/env python3
"""
笔记本命令处理器
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from modules.notebook.client import NotebookClient
from utils.format import OutputFormatter
from utils.tree import TreeRenderer
from core.exceptions import SiyuanError


class NotebookCommand:
    """笔记本命令处理器"""

    def __init__(self):
        self.client = NotebookClient()
        self.formatter = OutputFormatter()
        self.renderer = TreeRenderer()

    def list(self, tree: bool = False, format: str = "text", show_docs: bool = False):
        """
        列出所有笔记本
        
        Args:
            tree: 是否以树状结构显示
            format: 输出格式
            show_docs: 是否显示文档数量
        """
        try:
            notebooks = self.client.list_notebooks()

            if format == "json":
                if show_docs:
                    data = []
                    for nb in notebooks:
                        try:
                            from modules.document.client import DocumentClient
                            doc_client = DocumentClient()
                            docs = doc_client.list_documents(nb.id)
                            data.append({
                                "id": nb.id,
                                "name": nb.name,
                                "closed": nb.closed,
                                "doc_count": len(docs)
                            })
                        except:
                            data.append({
                                "id": nb.id,
                                "name": nb.name,
                                "closed": nb.closed,
                                "doc_count": 0
                            })
                else:
                    data = [{"id": nb.id, "name": nb.name, "closed": nb.closed}
                           for nb in notebooks]
                print(self.formatter.json(data))
                return

            if show_docs:
                from modules.document.client import DocumentClient
                doc_client = DocumentClient()

                print(f"\n📚 笔记本列表 ({len(notebooks)} 个)")
                print("=" * 80)

                for i, nb in enumerate(notebooks):
                    try:
                        docs = doc_client.list_documents(nb.id)
                        if tree:
                            # 以树状结构显示文档
                            docs_with_path = []
                            for doc in docs:
                                try:
                                    hpath = doc_client.get_hpath_by_id(doc.id)
                                    docs_with_path.append({
                                        "title": doc.title,
                                        "hpath": hpath
                                    })
                                except:
                                    docs_with_path.append({
                                        "title": doc.title,
                                        "hpath": doc.hpath
                                    })

                            # 笔记本图标
                            nb_icon = "📖" if not nb.closed else "📕"

                            if docs_with_path:
                                print(f"\n{nb_icon} {nb.name}")
                                self.renderer.render_documents(docs_with_path)
                            else:
                                print(f"\n{nb_icon} {nb.name} (空)")
                        else:
                            # 以列表形式显示文档
                            status = "" if not nb.closed else " [关闭]"
                            print(f"\n📁 {nb.name}{status} ({len(docs)} 个文档)")
                            print("-" * 80)
                            if docs:
                                for i, doc in enumerate(docs, 1):
                                    print(f"  {i}. {doc.title}")
                                    print(f"     ID: {doc.id}")
                                    print(f"     路径: {doc.hpath}")
                            else:
                                print("  (无文档)")
                    except Exception as e:
                        print(f"\n✗ {nb.name}: 获取文档列表失败 - {e}")
            elif tree:
                print(f"\n📚 笔本列表 ({len(notebooks)} 个)")
                print("=" * 60)
                self.renderer.render_notebooks(
                    [nb.__dict__ for nb in notebooks],
                    with_docs=False
                )
            else:
                print(f"\n📚 笔本列表 ({len(notebooks)} 个)")
                print("=" * 60)
                headers = ["名称", "ID", "状态"]
                rows = []
                for nb in notebooks:
                    status = "打开" if not nb.closed else "关闭"
                    rows.append([nb.name, nb.id, status])
                print(self.formatter.table(headers, rows))

        except SiyuanError as e:
            print(f"✗ 错误: {e}")

    def create(self, name: str):
        """
        创建笔记本
        
        Args:
            name: 笔记本名称
        """
        try:
            notebook = self.client.create_notebook(name)
            print(f"✓ 已创建笔记本: {notebook.name}")
            print(f"  ID: {notebook.id}")
        except SiyuanError as e:
            print(f"✗ 创建失败: {e}")

    def remove(self, notebook: str, yes: bool = False):
        """
        删除笔记本

        Args:
            notebook: 笔记本 ID 或名称
            yes: 是否跳过确认直接删除
        """
        try:
            # 尝试按 ID 查找
            try:
                nb = self.client.get_notebook(notebook)
            except:
                # 按 ID 查找失败，尝试按名称查找
                nb = self.client.find_notebook_by_name(notebook)

            if not yes:
                print(f"⚠️  即将删除笔记本: {nb.name}")
                confirm = input("确认删除? (yes/no): ").strip().lower()

                if confirm not in ['yes', 'y']:
                    print("操作已取消")
                    return

            self.client.remove_notebook(nb.id)
            print(f"✓ 已删除笔记本: {nb.name}")

        except SiyuanError as e:
            print(f"✗ 删除失败: {e}")

    def rename(self, notebook: str, name: str):
        """
        重命名笔记本
        
        Args:
            notebook: 笔记本 ID 或名称
            name: 新名称
        """
        try:
            # 尝试按 ID 查找
            try:
                nb = self.client.get_notebook(notebook)
            except:
                nb = self.client.find_notebook_by_name(notebook)
            
            self.client.rename_notebook(nb.id, name)
            print(f"✓ 已重命名笔记本: {nb.name} → {name}")
            
        except SiyuanError as e:
            print(f"✗ 重命名失败: {e}")

    def open(self, notebook: str):
        """
        打开笔记本
        
        Args:
            notebook: 笔记本 ID 或名称
        """
        try:
            try:
                nb = self.client.get_notebook(notebook)
            except:
                nb = self.client.find_notebook_by_name(notebook)
            
            self.client.open_notebook(nb.id)
            print(f"✓ 已打开笔记本: {nb.name}")
            
        except SiyuanError as e:
            print(f"✗ 打开失败: {e}")

    def close(self, notebook: str):
        """
        关闭笔记本
        
        Args:
            notebook: 笔记本 ID 或名称
        """
        try:
            try:
                nb = self.client.get_notebook(notebook)
            except:
                nb = self.client.find_notebook_by_name(notebook)
            
            self.client.close_notebook(nb.id)
            print(f"✓ 已关闭笔记本: {nb.name}")
            
        except SiyuanError as e:
            print(f"✗ 关闭失败: {e}")

    def conf(self, notebook: str, operation: str = "get", key: str = None, value: str = None):
        """
        笔记本配置操作

        Args:
            notebook: 笔记本 ID 或名称
            operation: 操作类型 (get/set)
            key: 配置键
            value: 配置值
        """
        try:
            try:
                nb = self.client.get_notebook(notebook)
            except:
                nb = self.client.find_notebook_by_name(notebook)

            if operation == "get":
                conf = self.client.get_conf(nb.id)
                print(f"\n📋 {nb.name} 配置:")
                print(self.formatter.json(conf))
            elif operation == "set":
                if not key or not value:
                    print("✗ 请提供配置键和值")
                    return
                conf = self.client.get_conf(nb.id)
                conf[key] = value
                self.client.set_conf(nb.id, conf)
                print(f"✓ 已设置配置: {key} = {value}")
                
        except SiyuanError as e:
            print(f"✗ 操作失败: {e}")
