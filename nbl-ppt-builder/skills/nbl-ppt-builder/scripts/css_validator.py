#!/usr/bin/env python3
"""
CSS 语法验证器（公共模块）

被 validate_with_playwright.py 和 validate_css.py 共用，统一返回结构：
  [{line, column, type, message, suggestion, context}, ...]

检测内容:
  1. Tailwind CSS 类名语法误用在 style 属性中（如 style="text-[#COLOR]"）
  2. 无效的 CSS 属性名（包含方括号等非法字符）
  3. 缺少冒号的 CSS 声明
  4. 颜色值格式错误
  5. 空属性名
"""

import re
from pathlib import Path


class CSSValidator:
    """CSS 语法验证器"""

    def __init__(self):
        # 注意：所有正则都包裹在 style="..." 边界内，避免误匹配 class 属性
        self.tailwind_patterns = [
            # 文本颜色: text-[#COLOR], text-COLOR
            re.compile(r'style=["\'][^"\']*?text-\[#?([a-fA-F0-9]{3,8}|[a-z]+)\][^"\']*?["\']'),
            # 背景颜色: bg-[#COLOR], bg-COLOR
            re.compile(r'style=["\'][^"\']*?bg-\[#?([a-fA-F0-9]{3,8}|[a-z]+)\][^"\']*?["\']'),
            # 字体大小: font-[SIZE]
            re.compile(r'style=["\'][^"\']*?font-\[(\d+\.?\d*(pt|px|em|rem|%)?)\][^"\']*?["\']'),
            # 内边距: p-[SIZE], px-[SIZE], py-[SIZE], pl-[SIZE], pr-[SIZE], pt-[SIZE], pb-[SIZE]
            re.compile(r'style=["\'][^"\']*(?:p|px|py|pl|pr|pt|pb)-\[(\d+\.?\d*(px|em|rem|%))\][^"\']*?["\']'),
            # 外边距: m-[SIZE], mx-[SIZE], my-[SIZE], ml-[SIZE], mr-[SIZE], mt-[SIZE], mb-[SIZE]
            re.compile(r'style=["\'][^"\']*(?:m|mx|my|ml|mr|mt|mb)-\[(\d+\.?\d*(px|em|rem|%))\][^"\']*?["\']'),
            # 方括号格式的任意属性
            re.compile(r'style=["\'][^"\']*[a-zA-Z]+-\[[^\]]*\][^"\']*?["\']'),
        ]

        self.invalid_property_chars = re.compile(r'\[|\]')

    def extract_style_declarations(self, html_content):
        """提取 HTML 中所有的 style 声明，返回包含行号和内容的信息"""
        results = []
        lines = html_content.split('\n')

        for line_num, line in enumerate(lines, start=1):
            match = re.search(r'style\s*=\s*["\']([^"\']+)["\']', line)
            if match:
                style_content = match.group(1)
                results.append({
                    'line': line_num,
                    'column': match.start(),
                    'style': style_content,
                    'full_match': match.group(0)
                })

        return results

    def validate_style(self, style_content):
        """验证单个 style 声明，返回错误列表"""
        errors = []

        # 检查1: Tailwind 类名语法
        for pattern in self.tailwind_patterns:
            if pattern.search('style="' + style_content + '"'):
                errors.append({
                    'type': 'TAILWIND_SYNTAX_IN_STYLE',
                    'message': '在style属性中使用了Tailwind CSS类名语法',
                    'suggestion': '请使用标准CSS语法，例如: color: #0B3BD3 而不是 text-[#0B3BD3]'
                })
                break

        # 检查2: 解析 CSS 声明
        declarations = style_content.split(';')
        for decl in declarations:
            decl = decl.strip()
            if not decl:
                continue

            if ':' not in decl:
                errors.append({
                    'type': 'MISSING_COLON',
                    'message': f'CSS声明缺少冒号: {decl}',
                    'suggestion': '格式应为: property: value;'
                })
                continue

            prop, value = decl.split(':', 1)

            if self.invalid_property_chars.search(prop):
                errors.append({
                    'type': 'INVALID_PROPERTY_NAME',
                    'message': f'CSS属性名包含非法字符: {prop.strip()}',
                    'suggestion': '属性名应只包含字母、数字和连字符'
                })

            if not prop.strip():
                errors.append({
                    'type': 'EMPTY_PROPERTY',
                    'message': 'CSS声明缺少属性名',
                    'suggestion': '格式应为: property: value;'
                })

        return errors

    def validate_html_file(self, html_file):
        """验证 HTML 文件中的 CSS 语法，返回统一的错误结构"""
        errors = []

        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()

        style_decls = self.extract_style_declarations(content)

        for decl in style_decls:
            decl_errors = self.validate_style(decl['style'])
            for error in decl_errors:
                errors.append({
                    'line': decl['line'],
                    'column': decl['column'],
                    'type': error['type'],
                    'message': error['message'],
                    'suggestion': error.get('suggestion', ''),
                    'context': decl['full_match']
                })

        return errors
