#!/usr/bin/env python3
"""
CSS语法验证器 - 命令行入口

复用 css_validator.CSSValidator 公共模块，提供独立的 CSS 语法检测命令行工具。
"""

import argparse
import sys
from pathlib import Path

from css_validator import CSSValidator


def print_results(html_file, errors):
    """打印单个文件的验证结果"""
    print(f'\n{"="*60}')
    print(f"📄 文件: {html_file}")
    print(f'{"="*60}')

    if not errors:
        print('✅ CSS语法验证通过 - 未发现语法错误')
        return 'ok'

    print(f'⚠️  发现 {len(errors)} 个CSS语法问题:\n')

    for i, error in enumerate(errors, 1):
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
    for p in args.paths:
        path = Path(p)
        if path.is_file() and path.suffix.lower() == '.html':
            html_files.append(path)
        elif path.is_dir():
            html_files.extend(sorted(path.glob('*.html')))

    if not html_files:
        print("❌ 未找到任何HTML文件")
        sys.exit(1)

    validator = CSSValidator()

    # 一次性验证并缓存结果（修复原先重复验证两次的性能问题）
    errors_by_file = {}
    all_errors_count = 0
    error_files = []

    for html_file in html_files:
        errors = validator.validate_html_file(html_file)
        errors_by_file[html_file] = errors
        print_results(html_file, errors)
        if errors:
            error_files.append(html_file)
            all_errors_count += len(errors)

    # 汇总报告
    print(f'\n{"="*60}')
    print('📊 CSS验证汇总报告')
    print(f'{"="*60}')
    print(f'  检测文件总数: {len(html_files)}')
    print(f'  ✅ 正常文件: {len(html_files) - len(error_files)}')
    print(f'  ❌ 错误文件: {len(error_files)}')
    print(f'  问题总数: {all_errors_count}')

    if error_files:
        print()
        print('📋 存在CSS问题的文件:')
        for f in error_files:
            print(f'  ❌ {f.name}')
    print()

    sys.exit(1 if all_errors_count > 0 else 0)


if __name__ == '__main__':
    main()
