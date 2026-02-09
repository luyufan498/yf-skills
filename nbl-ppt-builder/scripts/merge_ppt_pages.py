#!/usr/bin/env python3
"""
PPT HTML 页面拼接脚本
将多个按页码命名的 HTML 文件合并成一个完整的 HTML 文件
"""

import os
import re
import argparse
from pathlib import Path


def find_html_files(work_dir):
    """
    查找工作目录中所有按页码命名的 HTML 文件

    Args:
        work_dir: 工作目录路径

    Returns:
        list: 排序后的 (页码, 文件路径) 元组列表
    """
    html_files = []

    # 匹配格式: 02_描述.html, 02-描述.html, 02.描述.html 或 02.html
    pattern = re.compile(r"^(\d{2,3})[._-].*\.html$|^(\d{2,3})\.html$")

    for file in os.listdir(work_dir):
        if file.endswith(".html"):
            match = pattern.match(file)
            if match:
                # 提取页码
                page_num = int(match.group(1) or match.group(2))
                file_path = os.path.join(work_dir, file)
                html_files.append((page_num, file_path))

    # 按页码排序
    html_files.sort(key=lambda x: x[0])
    return html_files


def extract_body_content(html_content):
    """
    提取 HTML 文件中的 body 内容

    Args:
        html_content: HTML 文件内容

    Returns:
        str: body 标签内的内容
    """
    # 提取 body 标签内的内容
    body_match = re.search(
        r"<body[^>]*>(.*?)</body>", html_content, re.DOTALL | re.IGNORECASE
    )
    if body_match:
        return body_match.group(1)
    return ""


def extract_head_content(html_content):
    """
    提取 HTML 文件中的 head 内容（样式、脚本等）

    Args:
        html_content: HTML 文件内容

    Returns:
        tuple: (head内容, body内容)
    """
    # 提取 head 标签内的内容
    head_match = re.search(
        r"<head[^>]*>(.*?)</head>", html_content, re.DOTALL | re.IGNORECASE
    )
    head_content = head_match.group(1) if head_match else ""

    # 提取 body 标签内的内容
    body_match = re.search(
        r"<body[^>]*>(.*?)</body>", html_content, re.DOTALL | re.IGNORECASE
    )
    body_content = body_match.group(1) if body_match else ""

    return head_content, body_content


def deduplicate_scripts_and_styles(head_contents, body_contents):
    """
    去重脚本和样式引用，避免重复加载

    Args:
        head_contents: 所有页面的 head 内容列表
        body_contents: 所有页面的 body 内容列表

    Returns:
        tuple: (合并后的 head 内容列表, 原始 body 内容)
    """
    # 收集所有的样式和脚本标签
    seen_styles = set()
    seen_scripts = set()
    merged_head_contents = []

    for i, head in enumerate(head_contents):
        content = ""

        # 提取样式标签
        style_pattern = re.compile(
            r"<link[^>]*stylesheet[^>]*>|<style[^>]*>.*?</style>",
            re.DOTALL | re.IGNORECASE,
        )
        styles = style_pattern.findall(head)
        for style in styles:
            # 使用样式标签的哈希值作为唯一标识
            style_hash = hash(style)
            if style_hash not in seen_styles:
                seen_styles.add(style_hash)
                content += style + "\n"

        # 提取脚本标签（仅外部引用）
        script_pattern = re.compile(
            r'<script[^>]+src=["\']([^"\']+)["\'][^>]*></script>', re.IGNORECASE
        )
        scripts = script_pattern.findall(head)
        for script in scripts:
            if script not in seen_scripts:
                seen_scripts.add(script)
                # 重新构建完整的 script 标签
                script_tag = re.search(
                    r'<script[^>]+src=["\']'
                    + re.escape(script)
                    + r'["\'][^>]*></script>',
                    head,
                    re.IGNORECASE,
                )
                if script_tag:
                    content += script_tag.group(0) + "\n"

        # 保留内联脚本的第一个实例（通常是 ECharts 初始化等）
        inline_script_pattern = re.compile(
            r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE
        )
        inline_scripts = inline_script_pattern.findall(head)
        for script in inline_scripts:
            if "src=" not in script:  # 内联脚本
                script_hash = hash(script)
                if script_hash not in seen_scripts:
                    seen_scripts.add(script_hash)
                    content += script + "\n"

        merged_head_contents.append(content)

    return merged_head_contents, body_contents


def merge_html_files(html_files, output_file):
    """
    合并多个 HTML 文件

    Args:
        html_files: 排序后的 (页码, 文件路径) 元组列表
        output_file: 输出文件路径
    """
    if not html_files:
        print("错误：未找到任何 HTML 文件")
        return False

    print(f"找到 {len(html_files)} 个 HTML 文件")

    # 读取所有文件内容
    head_contents = []
    body_contents = []

    for page_num, file_path in html_files:
        print(f"读取页面 {page_num}: {os.path.basename(file_path)}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        head, body = extract_head_content(content)
        head_contents.append(head)
        body_contents.append((page_num, body))

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
            body {
                background: white;
            }
            .page {
                margin: 0;
                box-shadow: none;
            }
        }
    </style>
    </head>
    <body>
    """

    # 合并所有 body 内容，每页用 div.page 包裹
    for i, (page_num, body) in enumerate(body_contents):
        print(f"合并页面 {page_num}")
        merged_html += f"\n    <!-- Page {page_num} -->\n"
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
  python merge_ppt_pages.py -d ppt_季度总结_20240131
  python merge_ppt_pages.py -d ./ppt_产品介绍_20240131 -o merged_presentation.html
  python merge_ppt_pages.py -d ppt_季度总结 -o merged_presentation.html --verbose
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
        for page_num, file_path in html_files:
            print(f"  页码 {page_num}: {os.path.basename(file_path)}")
        print()

    # 构建输出文件路径
    output_path = os.path.join(args.dir, args.output)

    # 合并文件
    success = merge_html_files(html_files, output_path)

    if success:
        print(f"\n提示：在浏览器中打开以下文件查看完整 PPT:")
        print(f"  file://{os.path.abspath(output_path)}")
        return 0
    else:
        return 1


if __name__ == "__main__":
    exit(main())
