#!/usr/bin/env python3
"""
文档命令处理器
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from modules.document.client import DocumentClient
from modules.notebook.client import NotebookClient
from utils.format import OutputFormatter
from utils.tree import TreeRenderer
from core.exceptions import SiyuanError


class DocumentCommand:
    """文档命令处理器"""

    def __init__(self):
        self.client = DocumentClient()
        self.nb_client = NotebookClient()
        self.formatter = OutputFormatter()
        self.renderer = TreeRenderer()

    def show(self, doc_id: str, format: str = "text"):
        """
        显示文档内容
        
        Args:
            doc_id: 文档 ID
            format: 输出格式
        """
        try:
            doc = self.client.get_document(doc_id)
            
            if format == "json":
                import json
                data = {
                    "id": doc.id,
                    "title": doc.title,
                    "hpath": doc.hpath,
                    "content": doc.content
                }
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"\n📄 {doc.title}")
                print(f"路径: {doc.hpath}")
                print("=" * 60)
                print(doc.content)
                
        except SiyuanError as e:
            print(f"✗ 错误: {e}")

    def info(self, doc_id: str):
        """
        显示文档元信息
        
        Args:
            doc_id: 文档 ID
        """
        try:
            doc = self.client.get_document(doc_id)
            print(f"\n📋 文档信息")
            print("=" * 60)
            print(f"ID:     {doc.id}")
            print(f"标题:   {doc.title}")
            print(f"路径:   {doc.hpath}")
            print(f"长度:   {len(doc.content)} 字符")
            
        except SiyuanError as e:
            print(f"✗ 错误: {e}")

    def cat(self, doc_id: str):
        """
        在终端显示文档内容（纯文本）
        
        Args:
            doc_id: 文档 ID
        """
        try:
            doc = self.client.get_document(doc_id)
            print(doc.content)
            
        except SiyuanError as e:
            print(f"✗ 错误: {e}")

    def _remove_duplicate_title(self, content: str, title: str) -> str:
        """
        如果内容的第一行是标题（# title）且与 title 参数相同，则移除该行

        Args:
            content: 文档内容
            title: 文档标题

        Returns:
            处理后的文档内容
        """
        if not title or not content:
            return content

        lines = content.split('\n')
        if not lines:
            return content

        # 检查第一行是否为 # 标题格式
        first_line = lines[0].strip()
        expected_title = f"# {title}"

        if first_line == expected_title:
            # 移除第一行（标题）
            # 保留剩余内容，如果第二行是空行也一并移除
            remaining_lines = lines[1:]
            if remaining_lines and remaining_lines[0].strip() == "":
                remaining_lines = remaining_lines[1:]

            return '\n'.join(remaining_lines).lstrip('\n')

        return content

    def _upload_assets_if_needed(self, content: str, base_dir: str = ".") -> str:
        """
        上传文档中引用的资源文件（如果本地存在）

        Args:
            content: 文档内容
            base_dir: 基础目录（用于解析相对路径）

        Returns:
            更新后的文档内容
        """
        import re
        from pathlib import Path as PathLib
        from modules.asset.command import AssetCommand

        # 先检测所有图片引用（不限于 assets/）
        all_image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        all_images = re.findall(all_image_pattern, content)

        # 检查是否有非 assets/ 路径的图片
        non_asset_images = []
        for alt, img_path in all_images:
            # 跳过 assets/ 路径和 URL
            if not img_path.startswith("assets/") and not img_path.startswith("http"):
                non_asset_images.append(img_path)

        if non_asset_images:
            print("  ⚠️  警告：检测到非 assets/ 路径的图片引用：")
            for img_path in non_asset_images:
                print(f"     - {img_path}")
            print("     这些图片不会被自动上传，请手动调整资源路径为 assets/xxx")

        # 匹配 markdown 中的资源引用：![alt](assets/xxx.png) 或 ![alt](assets/xxx)
        pattern = r'!\[([^\]]*)\]\((assets/[^)]+)\)'
        matches = re.findall(pattern, content)

        if not matches:
            return content

        asset_cmd = AssetCommand()
        updated_content = content

        for alt, asset_path in matches:
            # 解析本地文件路径
            local_path = PathLib(base_dir) / asset_path

            if not local_path.exists():
                # 本地文件不存在，跳过
                continue

            try:
                # 上传文件到思源
                print(f"  上传资源: {asset_path}")
                result = asset_cmd.client.upload_file(str(local_path), "/assets/")

                if result and result.get("succMap"):
                    # succMap 格式：{"foo.png": "assets/foo-20210719092549-9j5y79r.png"}
                    # key 是原始文件名，value 是上传后的路径
                    original_filename = PathLib(asset_path).name
                    uploaded_path = result["succMap"].get(original_filename)

                    if uploaded_path:
                        # 更新文档中的引用
                        old_ref = f"![{alt}]({asset_path})"
                        new_ref = f"![{alt}]({uploaded_path})"
                        updated_content = updated_content.replace(old_ref, new_ref)

                        print(f"    → {PathLib(uploaded_path).name}")
            except Exception as e:
                print(f"    ✗ 上传失败: {e}")

        return updated_content

    def create(self, notebook: str, path: str, title: str = None,
              content: str = "", force: bool = False):
        """
        创建文档

        Args:
            notebook: 笔记本名称或 ID
            path: 父文档路径（如 "/" 或 "/父文档"）
            title: 文档标题（可选）
            content: 文档内容
            force: 强制创建，跳过父路径验证
        """
        try:
            nb = self.nb_client.find_notebook_by_name(notebook)

            # 验证父路径是否存在（除非使用 --force）
            if not force and not self.client.check_parent_path_exists(nb.id, path):
                print(f"✗ 错误: 父文档路径不存在: {path}")
                print()
                print("💡 提示:")
                print(f"   - 请检查路径是否正确")
                print(f"   - 如要强制创建（会以该路径创建新文档），请使用 --force 参数")
                print()
                print("⚠️  注意:")
                print(f"   - 'doc create' 的第2个参数是\"父文档路径\"，不是文档 ID")
                print(f"   - 路径格式: \"/\" (根目录) 或 \"/父文档/子文档\"")
                print(f"   - ID 格式: 20260127151833-5z7coxw（用于 move/rename/remove）")
                print()
                print("正确用法:")
                print(f'   python3 siyuan doc create "{notebook}" "/" --title "{title or "文档标题"}"')
                print(f'   python3 siyuan doc create "{notebook}" "/父文档路径" --title "{title or "子标题"}"')
                print()
                print("如果要在某个文档 ID 下创建子文档，请使用:")
                print(f"   步骤 1: python3 siyuan doc create \"{notebook}\" \"/\" --title \"{title or "文档标题"}\"")
                print(f"   步骤 2: python3 siyuan doc move <新文档ID> --to <父文档ID>")
                return

            # 自动移除 content 中的重复标题（如果第一行是 # title）
            content = self._remove_duplicate_title(content, title)

            # 自动上传资源文件（尝试多个可能的目录）
            import os
            from pathlib import Path as PathLib
            base_dirs = ["..", ".", "../.."]  # 优先尝试父目录，然后当前目录
            for base_dir in base_dirs:
                if (PathLib(base_dir) / "assets").exists():
                    content = self._upload_assets_if_needed(content, base_dir)
                    break

            if title:
                # 在路径中添加标题
                full_path = f"{path}/{title}".replace("//", "/")
            else:
                full_path = path

            doc_id = self.client.create_document(nb.id, full_path, content)
            print(f"✓ 已创建文档")
            print(f"  ID: {doc_id}")
            print(f"  路径: {full_path}")

        except SiyuanError as e:
            print(f"✗ 创建失败: {e}")

    def rename(self, doc_id: str, new_title: str):
        """
        重命名文档
        
        Args:
            doc_id: 文档 ID
            new_title: 新标题
        """
        try:
            self.client.rename_document(doc_id, new_title)
            print(f"✓ 已重命名文档: {new_title}")
            
        except SiyuanError as e:
            print(f"✗ 重命名失败: {e}")

    def move(self, doc_id: str, to: str):
        """
        移动文档

        Args:
            doc_id: 文档 ID
            to: 目标 ID（父文档 ID 或笔记本 ID）
        """
        try:
            self.client.move_document(doc_id, to)
            print(f"✓ 已移动文档")

        except SiyuanError as e:
            print(f"✗ 移动失败: {e}")

    def remove(self, doc_id: str, yes: bool = False):
        """
        删除文档

        Args:
            doc_id: 文档 ID
            yes: 直接确认，无需交互式提示
        """
        try:
            if not yes:
                print(f"⚠️  即将删除文档: {doc_id}")
                confirm = input("确认删除? (yes/no): ").strip().lower()

                if confirm not in ['yes', 'y']:
                    print("操作已取消")
                    return

            self.client.delete_document(doc_id)
            print(f"✓ 已删除文档: {doc_id}")

        except SiyuanError as e:
            print(f"✗ 删除失败: {e}")
        except EOFError:
            print(f"✗ 操作取消：无法获取用户确认（使用 -y 跳过确认）")

    def list(self, notebook: str, tree: bool = False, 
             filter: str = None):
        """
        列出笔记本下的文档
        
        Args:
            notebook: 笔记本名称
            tree: 是否以树状结构显示
            filter: 过滤关键词
        """
        try:
            nb = self.nb_client.find_notebook_by_name(notebook)
            docs = self.client.list_documents(nb.id)
            
            if filter:
                docs = [d for d in docs if filter.lower() in d.title.lower()]
            
            print(f"\n📚 {nb.name} - 文档列表 ({len(docs)} 个)")
            print("=" * 60)
            
            if tree:
                # 获取文档的完整路径信息
                docs_with_path = []
                for doc in docs:
                    result = self.client.get_hpath_by_id(doc.id)
                    docs_with_path.append({
                        "title": doc.title,
                        "hpath": result
                    })
                self.renderer.render_documents(docs_with_path)
            else:
                for i, doc in enumerate(docs, 1):
                    print(f"{i}. {doc.title}")
                    print(f"   ID: {doc.id}")
                    
        except SiyuanError as e:
            print(f"✗ 错误: {e}")
