#!/usr/bin/env python3
"""
导出命令处理器
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from core.client import SiyuanClient
from utils.format import OutputFormatter
from core.exceptions import SiyuanError


class ExportCommand:
    """导出命令处理器"""

    def __init__(self):
        self.client = SiyuanClient()

    def _remove_front_matter(self, content: str) -> str:
        """
        移除 front matter（YAML 元数据）

        Args:
            content: 原始 markdown 内容

        Returns:
            移除 front matter 后的内容
        """
        lines = content.split('\n')

        # 检查是否有 front matter（以 --- 开头）
        if len(lines) > 0 and lines[0].strip() == '---':
            # 找到第二个 --- 的位置
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == '---':
                    # 移除从开头到第二个 ---（含）的所有行
                    content = '\n'.join(lines[i+1:])
                    # 移除可能的前导空行
                    content = content.lstrip('\n')
                    return content

        # 没有 front matter，返回原内容
        return content

    def md(self, doc_id: str, output: str = None):
        """
        导出文档为 Markdown

        Args:
            doc_id: 文档 ID
            output: 输出文件路径（可选）
        """
        try:
            result = self.client.export_md_content(doc_id)
            content = result.get("content", "")

            # 自动移除 front matter
            content = self._remove_front_matter(content)

            hpath = result.get("hPath", "untitled").strip("/")

            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"✓ 已导出: {output}")
                print(f"  大小: {len(content)} 字符")
            else:
                print(content)

        except SiyuanError as e:
            print(f"✗ 导出失败: {e}")

    def zip(self, paths: list, name: str = None):
        """
        导出文件和目录为 ZIP
        
        Args:
            paths: 路径列表
            name: ZIP 文件名（可选）
        """
        try:
            result = self.client.export_resources(paths, name)
            zip_path = result.get("path", "")
            print(f"✓ 已导出: {zip_path}")
            
        except SiyuanError as e:
            print(f"✗ 导出失败: {e}")
