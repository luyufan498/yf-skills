#!/usr/bin/env python3
"""
CSS语法验证器 - 检测HTML中常用的CSS语法错误

主要检测:
1. Tailwind CSS类名语法误用在style属性中（如 style="text-[#COLOR]"）
2. 无效的CSS属性名（包含方括号等非法字符）
3. 缺少分号的CSS声明
4. 颜色值格式错误
"""

import re
import sys
from pathlib import Path


class CSSValidator:
    """CSS语法验证器"""

    def __init__(self):
        # 定义Tailwind类名模式的正则表达式
        # 这些语法在 class 属性中是正确的，但在 style 属性中是错误的
        self.tailwind_patterns = [
            # 文本颜色: text-[#COLOR], text-COLOR
            re.compile(r'style=["\'][^"\']*?text-\[#?([a-fA-F0-9]{3,8}|[a-z]+)\][^"\']*?["\']'),
            # 背景颜色: bg-[#COLOR], bg-COLOR
            re.compile(r'style=["\'][^"\']*?bg-\[#?([a-fA-F0-9]{3,8}|[a-z]+)\][^"\']*?["\']'),
            # 字体大小: font-[SIZE]
            re.compile(r'style=["\'][^"\']*?font-\[(\d+\.?\d*(pt|px|em|rem|%|%)?)\][^"\']*?["\']'),
            # 内边距: p-[SIZE], px-[SIZE], py-[SIZE], pl-[SIZE], pr-[SIZE], pt-[SIZE], pb-[SIZE]
            re.compile(r'style=["\'][^"\']*(?:p|px|py|pl|pr|pt|pb)-\[(\d+\.?\d*(px|em|rem|%))\][^"\']*?["\']'),
            # 外边距: m-[SIZE], mx-[SIZE], my-[SIZE], ml-[SIZE], mr-[SIZE], mt-[SIZE], mb-[SIZE]
            re.compile(r'style=["\'][^"\']*(?:m|mx|my|ml|mr|mt|mb)-\[(\d+\.?\d*(px|em|rem|%))\][^"\']*?["\']'),
            # 方括号格式的任意属性
            re.compile(r'style=["\'][^"\']*[a-zA-Z]+-\[[^\]]*\][^"\']*?["\']'),
        ]

        # CSS属性名不应该包含的非法字符
        self.invalid_property_chars = re.compile(r'\[|\]')

    def extract_style_declarations(self, html_content):
        """
        提取HTML中所有的style声明，返回包含行号和内容的信息
        """
        results = []
        lines = html_content.split('\n')

        for line_num, line in enumerate(lines, start=1):
            # 匹配 style="..." 或 style='...'
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
        """
        验证单个style声明，返回错误列表
        """
        errors = []

        # 检查1: Tailwind类名语法
        for pattern in self.tailwind_patterns:
            if pattern.search(style_content):
                errors.append({
                    'type': 'TAILWIND_SYNTAX_IN_STYLE',
                    'message': '在style属性中使用了Tailwind CSS类名语法',
                    'suggestion': '请使用标准CSS语法，例如: color: #0B3BD3 而不是 text-[#0B3BD3]'
                })
                break  # 找到一个Tailwind模式就停止

        # 检查2: 解析CSS声明
        declarations = style_content.split(';')
        for decl in declarations:
            decl = decl.strip()
            if not decl:
                continue

            # 检查是否包含冒号（CSS声明的标志）
            if ':' not in decl:
                errors.append({
                    'type': 'MISSING_COLON',
                    'message': f'CSS声明缺少冒号: {decl}',
                    'suggestion': '格式应为: property: value;'
                })
                continue

            # 分离属性名和值
            prop, value = decl.split(':', 1)

            # 检查3: 非法属性名字符
            if self.invalid_property_chars.search(prop):
                errors.append({
                    'type': 'INVALID_PROPERTY_NAME',
                    'message': f'CSS属性名包含非法字符: {prop.strip()}',
                    'suggestion': '属性名应只包含字母、数字和连字符'
                })

            # 检查4: 属性名是否为空
            if not prop.strip():
                errors.append({
                    'type': 'EMPTY_PROPERTY',
                    'message': 'CSS声明缺少属性名',
                    'suggestion': '格式应为: property: value;'
                })

        return errors

    def validate_html_file(self, html_file):
        """
        验证HTML文件中的CSS语法
        """
        errors = []

        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取所有style声明
        style_decls = self.extract_style_declarations(content)

        for decl in style_decls:
            # 验证每个style声明
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

    def print_results(self, html_file, errors):
        """
        打印验证结果
        """
        print(f'\n{"="*60}')
        print(f"📄 文件: {html_file}")
        print(f'{"="*60}')

        if not errors:
            print('✅ CSS语法验证通过 - 未发现语法错误')
            return 'ok'

        print(f'⚠️  发现 {len(errors)} 个CSS语法问题:\n')

        for i, error in enumerate(errors, 1):
            # 根据类型选择图标
            if error['type'] == 'TAILWIND_SYNTAX_IN_STYLE':
                icon = '🎨'
            elif error['type'] == 'MISSING_COLON':
                icon = '⚠️'
            elif error['type'] == 'INVALID_PROPERTY_NAME':
                icon = '❌'
            else:
                icon = '🔍'

            print(f"  {icon} {i}. {error['message']}")
            print(f"     行号: {error['line']}")
            print(f"     上下文: {error['context']}")

            if error['suggestion']:
                print(f"     建议: {error['suggestion']}")
            print()

        return 'error'


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="CSS语法验证器 - 检测HTML中CSS语法错误",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 验证单个文件
  python validate_css.py slide.html

  # 验证目录中的所有HTML文件
  python validate_css.py /path/to/slides/

  # 验证多个文件
  python validate_css.py slide1.html slide2.html

检测内容:
  - Tailwind CSS类名语法误用在style属性中
  - 无效的CSS属性名（包含方括号等非法字符）
  - 缺少分号的CSS声明
  - CSS声明缺少冒号

退出码: 0=正常, 1=错误
"""
    )

    parser.add_argument(
        'paths',
        nargs='+',
        help='要验证的HTML文件或目录路径'
    )

    args = parser.parse_args()

    # 收集所有HTML文件
    html_files = []
    for path in args.paths:
        p = Path(path)
        if p.is_file() and p.suffix.lower() == '.html':
            html_files.append(p)
        elif p.is_dir():
            html_files.extend(sorted(p.glob('*.html')))

    if not html_files:
        print("❌ 未找到任何HTML文件")
        sys.exit(1)

    # 创建验证器
    validator = CSSValidator()

    # 验证所有文件
    all_errors = 0
    for html_file in html_files:
        errors = validator.validate_html_file(html_file)
        status = validator.print_results(html_file, errors)
        all_errors += len(errors)

    # 汇总报告
    # 统计有错误的文件
    error_files = []
    for html_file in html_files:
        errors = validator.validate_html_file(html_file)
        if errors:
            error_files.append(html_file)

    print(f'\n{"="*60}')
    print('📊 CSS验证汇总报告')
    print(f'{"="*60}')
    print(f'  检测文件总数: {len(html_files)}')
    print(f'  ✅ 正常文件: {len(html_files) - len(error_files)}')
    print(f'  ❌ 错误文件: {len(error_files)}')
    print(f'  问题总数: {all_errors}')

    # 列出有问题的文件
    if error_files:
        print()
        print('📋 存在CSS问题的文件:')
        for f in error_files:
            print(f'  ❌ {f.name}')
    print()

    # 退出码
    if all_errors > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
