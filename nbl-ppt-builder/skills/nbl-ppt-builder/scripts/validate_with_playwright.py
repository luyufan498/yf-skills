#!/usr/bin/env python3
"""
PPT 页面验证器 - 使用 Playwright 真实检测滚动条和边界溢出 + CSS语法验证
"""

import argparse
import asyncio
import sys
import json
import os
import re
from pathlib import Path

# 保存原始工作目录（在import其他模块之前）
ORIGINAL_CWD = Path(os.getcwd())


# ==================== CSS 验证功能 ====================

class CSSValidator:
    """CSS语法验证器"""

    def __init__(self):
        # 定义Tailwind类名模式的正则表达式
        self.tailwind_patterns = [
            # 文本颜色: text-[#COLOR], text-COLOR
            re.compile(r'text-\[#?([a-fA-F0-9]{3,8}|[a-z]+)\]'),
            # 背景颜色: bg-[#COLOR], bg-COLOR
            re.compile(r'bg-\[#?([a-fA-F0-9]{3,8}|[a-z]+)\]'),
            # 字体大小: font-[SIZE]
            re.compile(r'font-\[(\d+\.?\d*(pt|px|em|rem|%|%)?)\]'),
            # 内边距: p-[SIZE], px-[SIZE], py-[SIZE], pl-[SIZE], pr-[SIZE], pt-[SIZE], pb-[SIZE]
            re.compile(r'(?:p|px|py|pl|pr|pt|pb)-\[(\d+\.?\d*(px|em|rem|%))\]'),
            # 外边距: m-[SIZE], mx-[SIZE], my-[SIZE], ml-[SIZE], mr-[SIZE], mt-[SIZE], mb-[SIZE]
            re.compile(r'(?:m|mx|my|ml|mr|mt|mb)-\[(\d+\.?\d*(px|em|rem|%))\]'),
            # 方括号格式的任意属性
            re.compile(r'[a-zA-Z]+-\[[^\]]*\]'),
        ]

    def validate_html_file(self, html_file):
        """
        验证HTML文件中的CSS语法，返回错误列表
        """
        errors = []

        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')

        for line_num, line in enumerate(lines, start=1):
            # 匹配 style="..." 或 style='...'
            match = re.search(r'style\s*=\s*["\']([^"\']+)["\']', line)
            if match:
                style_content = match.group(1)

                # 快速检测Tailwind类名语法
                for pattern in self.tailwind_patterns:
                    if pattern.search(style_content):
                        errors.append({
                            "type": "E",
                            "category": "css_syntax_error",
                            "severity": "high",
                            "description": f"CSS语法错误: 在style属性中使用了Tailwind class语法",
                            "details": {
                                "line": line_num,
                                "context": match.group(0),
                                "suggestion": "请使用标准CSS语法，例如: color: #0B3BD3 而不是 text-[#0B3BD3]"
                            },
                        })
                        break  # 找到一个错误后就停止检查该style属性

        return errors


def check_scroll_with_playwright(html_file):
    """使用 Playwright 真实检测滚动条"""
    issues = []

    try:
        # 使用 Playwright 实际检测滚动状态
        return asyncio.run(detect_with_playwright_async(html_file))

    except Exception as e:
        print(f"检测失败: {e}")
        script_dir = Path(__file__).parent
        print(f"💡 使用绝对路径执行:")
        print(f"   uv run --directory {script_dir} validate_with_playwright.py /path/to/slides/")
        print(f"   或切换到项目目录: cd <项目目录> && uv run --directory {script_dir} validate_with_playwright.py slides/")
        sys.exit(1)


async def detect_with_playwright_async(html_file):
    """使用 Playwright 检测内容是否溢出幻灯片底部"""
    from playwright.async_api import async_playwright

    issues = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 960, "height": 540})
        await page.goto(f"file://{Path(html_file).absolute()}")

        # 等待页面完全加载
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        # 获取 .slide-container 容器的位置（作为参考点）
        container_box = await page.evaluate("""
            () => {
                const container = document.querySelector('.slide-container');
                if (!container) return null;
                const rect = container.getBoundingClientRect();
                return {
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height
                };
            }
        """)

        if not container_box:
            await browser.close()
            return []

        # 检测所有绝对定位的同级元素（包括卡片和底部说明框等）
        # 策略：查找 .slide-container 下所有绝对定位的 div 元素
        cards = []

        # 查找所有绝对定位的直接子元素
        absolute_elements = await page.evaluate("""
            () => {
                const container = document.querySelector('.slide-container');
                if (!container) return [];

                const children = Array.from(container.children);
                const absoluteDivs = children.filter(el => {
                    // 只检测 div 元素
                    if (el.tagName.toLowerCase() !== 'div') return false;

                    // 获取计算样式，检查是否为绝对定位
                    const style = window.getComputedStyle(el);
                    if (style.position !== 'absolute') return false;

                    // 排除装饰性元素（pointer-events: none 或完全透明）
                    if (style.pointerEvents === 'none' || style.opacity === '0') return false;

                    // 排除包含 "pointer-events-none" 类的元素
                    if (el.classList.contains('pointer-events-none')) return false;

                    // 排除纯装饰性的分隔线（高度或宽度很小）
                    const rect = el.getBoundingClientRect();
                    if (rect.height < 3 || rect.width < 3) return false;

                    // 排除纯装饰性的背景块（没有文字内容的背景色块）
                    // 判断条件：没有子文本节点或文本为空，且设置了背景
                    const textContent = el.textContent.trim();
                    const hasText = textContent.length > 0;
                    const hasBackground = style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
                                         style.backgroundColor !== 'transparent';
                    const hasChildren = el.children.length > 0;

                    // 如果元素没有文本、没有子元素、但有背景色，认为是装饰性背景块
                    if (!hasText && !hasChildren && hasBackground) {
                        return false;
                    }

                    // 排除只包含图片的装饰性元素
                    // 判断条件：子元素只有img标签，没有其他文字内容
                    if (hasChildren && !hasText) {
                        const childElements = Array.from(el.children);
                        const onlyHasImages = childElements.every(child =>
                            child.tagName.toLowerCase() === 'img' ||
                            (child.tagName.toLowerCase() === 'div' && child.children.length === 0)
                        );
                        // 如果只包含图片或空div，认为是装饰性元素
                        if (onlyHasImages) {
                            return false;
                        }
                    }

                    // 排除页码元素（通常在右下角，只包含数字或很小的区域）
                    // 页码元素通常高度小于30px，且包含数字文本
                    if (rect.height < 30 && rect.width < 50) {
                        const text = el.textContent.trim();
                        // 如果只包含数字和少量文字，认为是页码
                        if (/^[\\d\\s]+$/.test(text) && text.length < 10) {
                            return false;
                        }
                    }

                    return true;
                });

                // 返回元素的索引，用于后续获取 bounding box
                return absoluteDivs.map((el, idx) => {
                    // 生成唯一标识
                    const classes = el.className || '';
                    const classList = classes.trim().split(/\\s+/).slice(0, 3).join('.');
                    return {
                        index: Array.from(container.children).indexOf(el),
                        className: classes,
                        elementId: 'div' + (classList ? '.' + classList : '')
                    };
                });
            }
        """)

        # 获取每个元素的 bounding box 和滚动信息
        for elem_info in absolute_elements:
            index = elem_info["index"]
            element_id = elem_info["elementId"]
            class_name = elem_info["className"]

            # 获取该索引位置的元素
            element = await page.query_selector(f".slide-container > div:nth-child({index + 1})")
            if not element:
                continue

            box = await element.bounding_box()
            if not box:
                continue

            # 计算相对于 .slide-container 的位置（而不是相对于viewport）
            relative_box = {
                "x": box["x"] - container_box["x"],
                "y": box["y"] - container_box["y"],
                "width": box["width"],
                "height": box["height"]
            }

            # 检测元素内部内容是否溢出容器
            scroll_info = await element.evaluate("""
                el => {
                    return {
                        scrollHeight: el.scrollHeight,
                        clientHeight: el.clientHeight,
                        scrollWidth: el.scrollWidth,
                        clientWidth: el.clientWidth,
                        hasVerticalOverflow: el.scrollHeight > el.clientHeight,
                        hasHorizontalOverflow: el.scrollWidth > el.clientWidth,
                        verticalOverflow: el.scrollHeight - el.clientHeight,
                        horizontalOverflow: el.scrollWidth - el.clientWidth
                    };
                }
            """)

            cards.append({
                "element": element,
                "box": relative_box,  # 使用相对于容器的位置
                "element_id": element_id,
                "tag_name": "div",
                "scroll_info": scroll_info,
                "class_name": class_name
            })

        # 检测卡片之间的重叠
        for i in range(len(cards)):
            for j in range(i + 1, len(cards)):
                card1 = cards[i]
                card2 = cards[j]

                # 检查是否重叠
                if boxes_overlap(card1["box"], card2["box"]):
                    overlap_area = calculate_overlap_area(card1["box"], card2["box"])
                    element_id1 = card1.get("element_id", f"卡片{i+1}")
                    element_id2 = card2.get("element_id", f"卡片{j+1}")

                    issues.append({
                        "type": "B",
                        "category": "card_overlap",
                        "severity": "high",
                        "description": f"卡片重叠: 元素重叠约 {overlap_area:.0f}px²",
                        "details": {
                            "card1": {
                                "top": card1["box"]["y"],
                                "left": card1["box"]["x"],
                                "width": card1["box"]["width"],
                                "height": card1["box"]["height"],
                                "bottom": card1["box"]["y"] + card1["box"]["height"],
                                "right": card1["box"]["x"] + card1["box"]["width"],
                                "element_id": element_id1,
                            },
                            "card2": {
                                "top": card2["box"]["y"],
                                "left": card2["box"]["x"],
                                "width": card2["box"]["width"],
                                "height": card2["box"]["height"],
                                "bottom": card2["box"]["y"] + card2["box"]["height"],
                                "right": card2["box"]["x"] + card2["box"]["width"],
                                "element_id": element_id2,
                            },
                            "overlap_area": overlap_area,
                        },
                    })

        # 检测内容溢出幻灯片底部
        slide_height = 540
        for card in cards:
            card_bottom = card["box"]["y"] + card["box"]["height"]

            if card_bottom > slide_height:
                overflow = card_bottom - slide_height
                element_id = card.get("element_id", "未命名元素")

                issues.append({
                    "type": "A",
                    "category": "content_overflow",
                    "severity": "high",
                    "description": f"内容溢出幻灯片底部: 超出 {overflow:.0f}px",
                    "details": {
                        "card_top": card["box"]["y"],
                        "card_height": card["box"]["height"],
                        "card_bottom": card_bottom,
                        "slide_height": slide_height,
                        "overflow": overflow,
                        "element_id": element_id,
                        "position": f"({card['box']['x']:.0f}, {card['box']['y']:.0f})",
                    },
                })

        # 检测卡片内部内容溢出容器
        for card in cards:
            scroll_info = card.get("scroll_info", {})
            element_id = card.get("element_id", "未命名元素")

            # 检测垂直内容溢出
            if scroll_info.get("hasVerticalOverflow", False):
                vertical_overflow = scroll_info.get("verticalOverflow", 0)
                issues.append({
                    "type": "C",
                    "category": "inner_content_overflow_vertical",
                    "severity": "high",
                    "description": f"卡片内部内容垂直溢出: 内容超出容器 {vertical_overflow:.0f}px",
                    "details": {
                        "card_top": card["box"]["y"],
                        "card_height": card["box"]["height"],
                        "content_height": scroll_info.get("scrollHeight", 0),
                        "container_height": scroll_info.get("clientHeight", 0),
                        "overflow": vertical_overflow,
                        "element_id": element_id,
                        "position": f"({card['box']['x']:.0f}, {card['box']['y']:.0f})",
                    },
                })

            # 检测水平内容溢出
            if scroll_info.get("hasHorizontalOverflow", False):
                horizontal_overflow = scroll_info.get("horizontalOverflow", 0)
                issues.append({
                    "type": "D",
                    "category": "inner_content_overflow_horizontal",
                    "severity": "high",
                    "description": f"卡片内部内容水平溢出: 内容超出容器 {horizontal_overflow:.0f}px",
                    "details": {
                        "card_left": card["box"]["x"],
                        "card_width": card["box"]["width"],
                        "content_width": scroll_info.get("scrollWidth", 0),
                        "container_width": scroll_info.get("clientWidth", 0),
                        "overflow": horizontal_overflow,
                        "element_id": element_id,
                        "position": f"({card['box']['x']:.0f}, {card['box']['y']:.0f})",
                    },
                })

        await browser.close()
        return issues


def boxes_overlap(box1, box2):
    """检查两个矩形是否重叠"""
    # box1 和 box 都是 {x, y, width, height} 格式
    x1_left = box1["x"]
    x1_right = box1["x"] + box1["width"]
    y1_top = box1["y"]
    y1_bottom = box1["y"] + box1["height"]

    x2_left = box2["x"]
    x2_right = box2["x"] + box2["width"]
    y2_top = box2["y"]
    y2_bottom = box2["y"] + box2["height"]

    # 检查是否相交（有重叠）
    overlap_x = x1_right > x2_left and x2_right > x1_left
    overlap_y = y1_bottom > y2_top and y2_bottom > y1_top

    return overlap_x and overlap_y


def calculate_overlap_area(box1, box2):
    """计算两个矩形的重叠面积"""
    # 计算重叠矩形的坐标
    x_overlap_left = max(box1["x"], box2["x"])
    x_overlap_right = min(box1["x"] + box1["width"], box2["x"] + box2["width"])
    y_overlap_top = max(box1["y"], box2["y"])
    y_overlap_bottom = min(box1["y"] + box1["height"], box2["y"] + box2["height"])

    # 计算重叠面积
    overlap_width = max(0, x_overlap_right - x_overlap_left)
    overlap_height = max(0, y_overlap_bottom - y_overlap_top)

    return overlap_width * overlap_height


def get_css_selector(el):
    """获取元素的CSS选择器"""
    if el.get("id"):
        return f"#{el.get('id')}"
    elif el.get("class"):
        classes = el.get("class", "").split(" ")
        return f"{el.tag_name.lower()}.{classes[0]}"
    else:
        return el.tag_name.lower()


def collect_html_files(paths):
    """收集所有要检测的 HTML 文件"""
    html_files = []

    for path in paths:
        p = Path(path)

        if p.is_file():
            if p.suffix.lower() == '.html':
                html_files.append(p)
        elif p.is_dir():
            # 收集目录下所有 HTML 文件
            html_files.extend(sorted(p.glob("*.html")))

    return html_files


def print_single_file_result(html_file, issues):
    """打印单个文件的检测结果"""
    print(f"\n{'='*60}")
    print(f"📄 文件: {html_file}")
    print(f"{'='*60}")

    if not issues:
        print("✅ 正常 - 未发现问题")
        return "ok"

    # 统计不同类型的问题
    overflow_count = sum(1 for i in issues if i["category"] == "content_overflow")
    overlap_count = sum(1 for i in issues if i["category"] == "card_overlap")
    inner_scroll_v_count = sum(1 for i in issues if i["category"] == "inner_content_overflow_vertical")
    inner_scroll_h_count = sum(1 for i in issues if i["category"] == "inner_content_overflow_horizontal")
    css_syntax_count = sum(1 for i in issues if i["category"] == "css_syntax_error")

    print(f"⚠️  发现 {len(issues)} 个问题:")
    if overflow_count > 0:
        print(f"  - 内容溢出幻灯片: {overflow_count} 个")
    if overlap_count > 0:
        print(f"  - 卡片重叠: {overlap_count} 个")
    if inner_scroll_v_count > 0:
        print(f"  - 卡片内部垂直滚动: {inner_scroll_v_count} 个")
    if inner_scroll_h_count > 0:
        print(f"  - 卡片内部水平滚动: {inner_scroll_h_count} 个")
    if css_syntax_count > 0:
        print(f"  - CSS语法错误: {css_syntax_count} 个")
    print()

    for i, issue in enumerate(issues, 1):
        # 根据问题类型选择图标
        if issue["category"] == "card_overlap":
            issue_type = "📌"
        elif issue["category"] == "inner_content_overflow_vertical":
            issue_type = "📜⬇️"
        elif issue["category"] == "inner_content_overflow_horizontal":
            issue_type = "📜➡️"
        elif issue["category"] == "css_syntax_error":
            issue_type = "🎨"
        else:
            issue_type = "⬇️"

        print(f"  {issue_type} {i}. {issue['description']}")
        details = issue.get("details", {})

        # CSS语法错误的特殊处理
        if issue["category"] == "css_syntax_error":
            print(f"      行号: {details.get('line', '?')}")
            print(f"      上下文: {details.get('context', '')}")
            if details.get('suggestion'):
                print(f"      建议: {details['suggestion']}")
            print()
            continue

        # 显示元素标识信息（适用于布局问题）
        element_id = details.get("element_id", "")
        position = details.get("position", "")
        if element_id:
            print(f"      元素: {element_id}")
        if position:
            print(f"      页面坐标: {position}")

        if issue["category"] == "content_overflow":
            print(f"      卡片尺寸: 顶部={details['card_top']:.0f}px, 高度={details['card_height']:.0f}px")
            print(f"      底部边界: {details['card_bottom']:.0f}px > 幻灯片 (540px)")
            print(f"      溢出量: {details['overflow']:.0f}px")
        elif issue["category"] == "card_overlap":
            card1 = details["card1"]
            card2 = details["card2"]
            print(f"      元素1: {card1.get('element_id', '未命名')}")
            print(f"        位置: (x={card1['left']:.0f}, y={card1['top']:.0f}, 宽={card1['width']:.0f}, 高={card1['height']:.0f})")
            print(f"      元素2: {card2.get('element_id', '未命名')}")
            print(f"        位置: (x={card2['left']:.0f}, y={card2['top']:.0f}, 宽={card2['width']:.0f}, 高={card2['height']:.0f})")
            print(f"      重叠面积: {details['overlap_area']:.0f}px²")
        elif issue["category"] == "inner_content_overflow_vertical":
            print(f"      容器尺寸: 高度={details['container_height']:.0f}px")
            print(f"      内容高度: {details['content_height']:.0f}px > 容器高度")
            print(f"      溢出量: {details['overflow']:.0f}px")
        elif issue["category"] == "inner_content_overflow_horizontal":
            print(f"      容器尺寸: 宽度={details['container_width']:.0f}px")
            print(f"      内容宽度: {details['content_width']:.0f}px > 容器宽度")
            print(f"      溢出量: {details['overflow']:.0f}px")
        print()

    # 返回状态
    has_high = any(issue["severity"] == "high" for issue in issues)
    return "error" if has_high else "warning"


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="PPT 页面验证器 - 使用 Playwright 检测布局问题 + CSS语法验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检测单个文件
  python validate_with_playwright.py presentation.html

  # 检测多个文件
  python validate_with_playwright.py slide1.html slide2.html slide3.html

  # 检测整个目录
  python validate_with_playwright.py /path/to/ppt_slides/

  # 混合检测文件和目录
  python validate_with_playwright.py slide1.html /path/to/slides/

  # 指定输出报告路径
  python validate_with_playwright.py /path/to/slides/ -o /path/to/report.json

检测内容:
  - 内容溢出幻灯片底部 (16:9 比例, 高度 540px)
  - 卡片之间的重叠
  - 卡片内部内容垂直溢出 (内容超出卡片高度)
  - 卡片内部内容水平溢出 (内容超出卡片宽度)
  - CSS语法错误 (Tailwind类名误用在style属性中)

输出:
  - 终端显示检测结果的详细信息
  - 生成 validation_report.json 文件 (包含所有问题的详细数据)
  - 退出码: 0=正常, 1=警告, 2=错误

环境要求:
  - Python 3.7+
  - Playwright Chromium 浏览器 (首次运行会自动安装)
"""
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="要验证的 HTML 文件或目录路径（支持多个文件/目录）"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出报告的 JSON 文件路径（不指定则不输出 JSON 文件）"
    )

    args = parser.parse_args()

    # 收集所有 HTML 文件
    html_files = collect_html_files(args.paths)

    if not html_files:
        print("❌ 未找到任何 HTML 文件")
        sys.exit(1)

    print(f"🔍 开始检测 {len(html_files)} 个文件...")
    print()

    # 批量检测结果
    all_results = []
    summary = {
        "total_files": len(html_files),
        "ok_files": 0,
        "warning_files": 0,
        "error_files": 0,
        "total_issues": 0,
        "issues_by_category": {
            "content_overflow": 0,
            "card_overlap": 0,
            "inner_content_overflow_vertical": 0,
            "inner_content_overflow_horizontal": 0,
            "css_syntax_error": 0,
        }
    }

    # 创建CSS验证器
    css_validator = CSSValidator()

    for html_file in html_files:
        # 第一步: Layout检测 (Playwright)
        layout_issues = check_scroll_with_playwright(str(html_file))

        # 第二步: CSS语法检测
        css_issues = css_validator.validate_html_file(str(html_file))

        # 合并问题列表
        all_issues = layout_issues + css_issues

        status = print_single_file_result(html_file, all_issues)

        # 统计
        if status == "ok":
            summary["ok_files"] += 1
        elif status == "warning":
            summary["warning_files"] += 1
        else:
            summary["error_files"] += 1

        summary["total_issues"] += len(all_issues)
        for issue in all_issues:
            cat = issue.get("category", "")
            if cat in summary["issues_by_category"]:
                summary["issues_by_category"][cat] += 1

        all_results.append({
            "file": str(html_file),
            "status": status,
            "issue_count": len(all_issues),
            "issues": all_issues
        })

    # 打印汇总报告
    print("\n" + "=" * 60)
    print("📊 检测汇总报告")
    print("=" * 60)
    print(f"  检测文件总数: {summary['total_files']}")
    print(f"  ✅ 正常文件: {summary['ok_files']}")
    print(f"  ⚠️  警告文件: {summary['warning_files']}")
    print(f"  ❌ 错误文件: {summary['error_files']}")
    print()
    print(f"  问题总数: {summary['total_issues']}")
    if summary["issues_by_category"]["content_overflow"] > 0:
        print(f"    - 内容溢出幻灯片: {summary['issues_by_category']['content_overflow']}")
    if summary["issues_by_category"]["card_overlap"] > 0:
        print(f"    - 卡片重叠: {summary['issues_by_category']['card_overlap']}")
    if summary["issues_by_category"]["inner_content_overflow_vertical"] > 0:
        print(f"    - 卡片内部内容垂直溢出: {summary['issues_by_category']['inner_content_overflow_vertical']}")
    if summary["issues_by_category"]["inner_content_overflow_horizontal"] > 0:
        print(f"    - 卡片内部内容水平溢出: {summary['issues_by_category']['inner_content_overflow_horizontal']}")
    if summary["issues_by_category"]["css_syntax_error"] > 0:
        print(f"    - CSS语法错误: {summary['issues_by_category']['css_syntax_error']}")

    # 列出有问题的文件
    problem_files = [r for r in all_results if r["status"] != "ok"]
    if problem_files:
        print()
        print("📋 问题文件列表:")
        for r in problem_files:
            status_icon = "❌" if r["status"] == "error" else "⚠️"
            print(f"  {status_icon} {Path(r['file']).name} ({r['issue_count']} 个问题)")

    # 保存 JSON 报告（仅当指定输出路径时）
    if args.output:
        result = {
            "summary": summary,
            "files": all_results
        }

        output_path = Path(args.output)
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print()
        print(f"✅ 报告已保存: {output_path}")
    print()

    # 退出码
    if summary["error_files"] > 0:
        sys.exit(2)
    elif summary["warning_files"] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
