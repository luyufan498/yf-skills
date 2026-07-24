#!/usr/bin/env python3
"""
PPT HTML 页面拼接脚本
将多个按页码命名的 HTML 文件合并成一个完整的 HTML 文件
"""

import os
import re
import argparse
from pathlib import Path


# 页码合理范围上限（用于 renumber 时识别页码 span）
MAX_PAGE_NUMBER = 100


def parse_page_key(filename):
    """
    解析文件名中的页码排序键

    支持正常页码 (01, 02...) 和插页 (03a, 03b...)

    Args:
        filename: 文件名

    Returns:
        tuple: (页码数字, 字母后缀) 或 None（不匹配时）
    """
    # 匹配格式: 02_描述.html, 02a_补充.html, 02b_案例.html
    pattern = re.compile(r"^(\d{2,3})([a-z]?)[._-].*\.html$")
    match = pattern.match(filename)
    if match:
        num = int(match.group(1))
        suffix = match.group(2)  # 空字符串或 "a", "b" 等
        return (num, suffix)
    return None


def find_html_files(work_dir):
    """
    查找工作目录中所有按页码命名的 HTML 文件

    支持插页格式: 03a_补充.html, 03b_案例.html

    Args:
        work_dir: 工作目录路径

    Returns:
        list: 排序后的 (页码键, 文件路径) 元组列表
                 页码键格式: (数字, 字母后缀) 如 (3, ""), (3, "a"), (3, "b")
    """
    html_files = []

    for file in os.listdir(work_dir):
        if file.endswith(".html"):
            page_key = parse_page_key(file)
            if page_key:
                file_path = os.path.join(work_dir, file)
                html_files.append((page_key, file_path))

    # 按 (数字, 字母后缀) 排序: (3, "") < (3, "a") < (3, "b") < (4, "")
    html_files.sort(key=lambda x: x[0])
    return html_files


def extract_head_content(html_content):
    """
    提取 HTML 文件中的 head 和 body 内容

    Args:
        html_content: HTML 文件内容

    Returns:
        tuple: (head内容, body内容)
    """
    head_match = re.search(
        r"<head[^>]*>(.*?)</head>", html_content, re.DOTALL | re.IGNORECASE
    )
    head_content = head_match.group(1) if head_match else ""

    body_match = re.search(
        r"<body[^>]*>(.*?)</body>", html_content, re.DOTALL | re.IGNORECASE
    )
    body_content = body_match.group(1) if body_match else ""

    return head_content, body_content


def deduplicate_scripts_and_styles(head_contents, body_contents):
    """
    去重外部样式表和外部脚本引用，避免重复加载

    重要：内联脚本（<script>...</script>）始终保留，不去重。
    原因：每个页面的内联脚本通常包含页面级初始化（ECharts option、容器 ID、
    变量赋值等），即使代码文本完全相同，其执行上下文也依赖所在页面的 DOM，
    去重会丢失页面级初始化逻辑。

    Args:
        head_contents: 所有页面的 head 内容列表
        body_contents: 所有页面的 body 内容列表（原样返回，未使用）

    Returns:
        tuple: (合并后的 head 内容列表, 原始 body 内容)
    """
    seen_styles = set()       # 用字符串本身作 key（稳定，不依赖 PYTHONHASHSEED）
    seen_external_scripts = set()  # 外部 script src
    merged_head_contents = []

    for head in head_contents:
        content = ""

        # 提取样式标签（<link rel="stylesheet"> 和 <style>...</style>）
        style_pattern = re.compile(
            r"<link[^>]*stylesheet[^>]*>|<style[^>]*>.*?</style>",
            re.DOTALL | re.IGNORECASE,
        )
        for style in style_pattern.findall(head):
            if style not in seen_styles:
                seen_styles.add(style)
                content += style + "\n"

        # 提取外部脚本标签（<script src="..."></script>）—— 仅外部引用参与去重
        external_script_pattern = re.compile(
            r'<script[^>]+src=["\']([^"\']+)["\'][^>]*></script>',
            re.IGNORECASE,
        )
        for match in external_script_pattern.finditer(head):
            src = match.group(1)
            if src not in seen_external_scripts:
                seen_external_scripts.add(src)
                content += match.group(0) + "\n"

        # 内联脚本：全部保留，不去重（每页独立上下文）
        inline_script_pattern = re.compile(
            r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>",
            re.DOTALL | re.IGNORECASE,
        )
        for script in inline_script_pattern.findall(head):
            content += script + "\n"

        merged_head_contents.append(content)

    return merged_head_contents, body_contents


def renumber_page_in_body(body_content, new_page_num):
    """
    将 body 内容中的页码数字替换为新页码

    策略（按优先级匹配）：
      1. 优先匹配带 page-number / pagination 类的 <span>（推荐做法）
      2. 兜底：匹配所有 <span>数字</span>，取最后一个合理范围(1-MAX_PAGE_NUMBER)的作为页码

    Args:
        body_content: body 标签内的 HTML 内容
        new_page_num: 新页码数字

    Returns:
        str: 替换页码后的 body 内容
    """
    # 优先：带 page-number / pagination 类的 span
    pattern_class = re.compile(
        r'(<span[^>]*\bclass=["\'][^"\']*\b(?:page-number|pagination)\b[^"\']*["\'][^>]*>)\s*(\d+)\s*(</span>)',
        re.IGNORECASE,
    )
    matches = list(pattern_class.finditer(body_content))
    if matches:
        last_match = matches[-1]
        start, end = last_match.span()
        return (
            body_content[:start]
            + last_match.group(1) + str(new_page_num) + last_match.group(3)
            + body_content[end:]
        )

    # 兜底：所有 <span>数字</span>，取最后一个合理页码
    pattern = re.compile(r"(<span[^>]*>)\s*(\d+)\s*(</span>)")
    valid_matches = [
        m for m in pattern.finditer(body_content)
        if 1 <= int(m.group(2)) <= MAX_PAGE_NUMBER
    ]
    if valid_matches:
        last_match = valid_matches[-1]
        start, end = last_match.span()
        return (
            body_content[:start]
            + last_match.group(1) + str(new_page_num) + last_match.group(3)
            + body_content[end:]
        )

    return body_content


def merge_html_files(html_files, output_file, renumber=False):
    """
    合并多个 HTML 文件

    Args:
        html_files: 排序后的 (页码键, 文件路径) 元组列表
                     页码键格式: (数字, 字母后缀) 如 (3, ""), (3, "a")
        output_file: 输出文件路径
        renumber: 是否重排页码（按合并后的顺序分配连续页码）
    """
    if not html_files:
        print("错误：未找到任何 HTML 文件")
        return False

    print(f"找到 {len(html_files)} 个 HTML 文件")

    # 读取所有文件内容
    head_contents = []
    body_contents = []

    for page_key, file_path in html_files:
        num, suffix = page_key
        display_key = f"{num:02d}{suffix}" if suffix else f"{num:02d}"
        print(f"读取页面 {display_key}: {os.path.basename(file_path)}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        head, body = extract_head_content(content)
        head_contents.append(head)
        body_contents.append((page_key, body))

    # 去重样式和脚本
    print("去重样式和脚本...")
    merged_head_contents, body_contents = deduplicate_scripts_and_styles(
        head_contents, body_contents
    )

    # 构建合并后的 HTML
    merged_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NBL PPT Presentation</title>
"""

    # 添加去重后的 head 内容
    for head in merged_head_contents:
        merged_html += head + "\n"

    merged_html += """    <style>
        /* 页面分页样式 */
        html, body {
            margin: 0;
            padding: 0;
            background: #f5f5f5;
            height: 100%;
            overflow-y: auto;
        }
        body {
            min-height: 100%;
            padding-bottom: 40px;
        }
        .page {
            page-break-after: always;
            width: 960px;
            height: 540px;
            margin: 0 auto 20px;
            background: white;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
            position: relative;
            overflow: hidden;
        }
        @media print {
            html, body {
                height: auto;
                overflow-y: visible;
            }
            body {
                background: white;
                padding-bottom: 0;
            }
            .page {
                margin: 0;
                box-shadow: none;
                page-break-inside: avoid;
            }
            .page:last-child {
                page-break-after: auto;
            }
        }
    </style>
    </head>
    <body>
    """

    # 合并所有 body 内容，每页用 div.page 包裹
    for i, (page_key, body) in enumerate(body_contents):
        num, suffix = page_key
        display_key = f"{num:02d}{suffix}" if suffix else f"{num:02d}"
        new_page_num = i + 1  # 合并后的连续页码（1-based）

        # 如果启用重排，替换 body 中的页码数字
        if renumber:
            body = renumber_page_in_body(body, new_page_num)

        print(f"合并页面 {display_key} → 新页码 {new_page_num}")
        merged_html += f"\n    <!-- Page {new_page_num} (原 {display_key}) -->\n"
        merged_html += f'    <div class="page">\n'
        merged_html += body
        merged_html += "\n    </div>\n"

    merged_html += """</body>
</html>"""

    # 写入输出文件
    print(f"正在生成合并文件: {output_file}")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(merged_html)

    print(f"\n✓ 成功生成 {output_file}")
    print(f"  共合并 {len(html_files)} 个页面")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="PPT HTML 页面拼接脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 普通合并（保持原始页码）
  python merge_ppt_pages.py -d ppt_季度总结_20240131

  # 重排页码后合并（推荐用于有拆分插页的PPT）
  python merge_ppt_pages.py -d ppt_季度总结_20240131 --renumber

  # 指定输出文件名
  python merge_ppt_pages.py -d ./ppt_产品介绍 -o merged_presentation.html --renumber
        """,
    )

    parser.add_argument(
        "-d", "--dir", required=True, help="工作目录路径（包含按页码命名的 HTML 文件）"
    )

    parser.add_argument(
        "-o",
        "--output",
        default="merged_presentation.html",
        help="输出文件名（默认: merged_presentation.html）",
    )

    parser.add_argument(
        "--renumber",
        action="store_true",
        help="重排页码：按文件排序后的顺序分配连续页码（1, 2, 3...），并替换每页内部显示的页码数字",
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="显示详细信息")

    args = parser.parse_args()

    # 检查目录是否存在
    if not os.path.isdir(args.dir):
        print(f"错误：目录不存在 - {args.dir}")
        return 1

    # 查找 HTML 文件
    html_files = find_html_files(args.dir)

    if not html_files:
        print(f"错误：在目录 {args.dir} 中未找到任何按页码命名的 HTML 文件")
        print("期望的文件名格式: 01_home.html, 02_toc.html, 03_背景介绍.html 等")
        return 1

    if args.verbose:
        print("\n找到以下 HTML 文件:")
        for page_key, file_path in html_files:
            num, suffix = page_key
            display_key = f"{num:02d}{suffix}" if suffix else f"{num:02d}"
            print(f"  页码 {display_key}: {os.path.basename(file_path)}")
        print()

    # 构建输出文件路径
    output_path = os.path.join(args.dir, args.output)

    # 合并文件
    success = merge_html_files(html_files, output_path, renumber=args.renumber)

    if success:
        print(f"\n提示：在浏览器中打开以下文件查看完整 PPT:")
        print(f"  file://{os.path.abspath(output_path)}")
        return 0
    else:
        return 1


if __name__ == "__main__":
    exit(main())
