#!/usr/bin/env python3
"""
输出格式化工具
"""

import json
from typing import Any, List


class OutputFormatter:
    """输出格式化器"""

    @staticmethod
    def text(data: Any, template=None) -> str:
        """
        文本格式输出（默认）
        
        Args:
            data: 要格式化的数据
            template: 可选的模板字符串
            
        Returns:
            格式化后的文本
        """
        if template:
            return template.format(**data)
        return str(data)

    @staticmethod
    def json(data: Any) -> str:
        """
        JSON 格式输出
        
        Args:
            data: 要格式化的数据
            
        Returns:
            JSON 字符串
        """
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def yaml(data: Any) -> str:
        """
        YAML 格式输出
        
        Args:
            data: 要格式化的数据
            
        Returns:
            YAML 字符串
        """
        try:
            import yaml
            return yaml.dump(data, allow_unicode=True, default_flow_style=False)
        except ImportError:
            # 如果没有 pyyaml，回退到 JSON
            return "# YAML format not available, falling back to JSON\n" + OutputFormatter.json(data)

    @staticmethod
    def table(headers: List[str], rows: List[List[str]]) -> str:
        """
        表格格式输出
        
        Args:
            headers: 表头列表
            rows: 行数据列表
            
        Returns:
            格式化的表格字符串
        """
        try:
            from tabulate import tabulate
            return tabulate(rows, headers=headers, tablefmt="grid")
        except ImportError:
            # 简单的表格实现
            return OutputFormatter._simple_table(headers, rows)

    @staticmethod
    def _simple_table(headers: List[str], rows: List[List[str]]) -> str:
        """简单的表格实现（无 tabulate 时使用）- 正确处理中文宽度"""
        def get_display_width(text: str) -> int:
            """计算字符串的显示宽度（中文按2计算）"""
            width = 0
            for char in text:
                # 判断是否为宽字符（中文、日文、韩文等）
                if '\u4e00' <= char <= '\u9fff':  # 中日韩字符
                    width += 2
                elif ord(char) > 127:  # 其他非ASCII字符
                    width += 1
                else:
                    width += 1
            return width

        # 计算每列的最大显示宽度
        col_widths = [get_display_width(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row[:len(col_widths)]):
                display_width = get_display_width(str(cell))
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], display_width)

        # 构建分隔线
        separator = "+" + "+".join(["-" * (w + 2) for w in col_widths]) + "+"

        # 构建表头
        lines = [separator]
        header_cells = [f" {h:<{col_widths[i]}} " for i, h in enumerate(headers)]
        lines.append("|" + "|".join(header_cells) + "|")
        lines.append(separator)

        # 构建数据行
        for row in rows:
            cells = []
            for i, cell in enumerate(row[:len(col_widths)]):
                # 左对齐，使用 str() 确保类型正确
                cell_str = str(cell)
                # 填充到正确宽度（注意中文字符）
                current_width = get_display_width(cell_str)
                padding = col_widths[i] - current_width
                cells.append(f" {cell_str}{' ' * padding}")
            lines.append("|" + "|".join(cells) + "|")

        lines.append(separator)
        return "\n".join(lines)
