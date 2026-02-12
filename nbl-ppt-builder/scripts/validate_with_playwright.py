#!/usr/bin/env python3
"""
PPT 页面验证器 - 使用 Playwright 真实检测滚动条和边界溢出
"""

import argparse
import asyncio
import sys
import json
from pathlib import Path


def check_scroll_with_playwright(html_file):
    """使用 Playwright 真实检测滚动条"""
    issues = []

    try:
        # 使用 Playwright 实际检测滚动状态
        return asyncio.run(detect_with_playwright_async(html_file))

    except Exception as e:
        print(f"检测失败: {e}")
        return []


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

        # 只检测主卡片（使用 .card-shadow），避免检测内部容器如 .rounded-xl
        card_selectors = [".card-shadow"]
        cards = []

        for selector in card_selectors:
            elements = await page.query_selector_all(selector)
            for element in elements:
                box = await element.bounding_box()
                if box:
                    # 获取元素本身的标识信息（标签名 + class）
                    element_info = await element.evaluate("""
                        el => {
                            // 获取元素本身的标签和完整 class
                            const tagName = el.tagName.toLowerCase();
                            const className = el.className || '';
                            return {
                                tagName: tagName,
                                className: className,
                                // 生成简洁的元素标识：标签名 + class
                                elementId: tagName + (className ? '.' + className.trim().split(/\\s+/).join('.') : '')
                            };
                        }
                    """)

                    tag_name = element_info.get("tagName", "div")
                    class_name = element_info.get("className", "")
                    element_id = element_info.get("elementId", "div")
                    # 通过位置去重，避免同一个元素被多次匹配
                    if not any(abs(c["box"]["x"] - box["x"]) < 1 and abs(c["box"]["y"] - box["y"]) < 1 for c in cards):
                        element_name = await element.evaluate("el => el.className")

                        # 检测元素内部滚动条
                        scroll_info = await element.evaluate("""
                            el => {
                                return {
                                    scrollHeight: el.scrollHeight,
                                    clientHeight: el.clientHeight,
                                    scrollWidth: el.scrollWidth,
                                    clientWidth: el.clientWidth,
                                    hasVerticalScroll: el.scrollHeight > el.clientHeight,
                                    hasHorizontalScroll: el.scrollWidth > el.clientWidth,
                                    verticalOverflow: el.scrollHeight - el.clientHeight,
                                    horizontalOverflow: el.scrollWidth - el.clientWidth
                                };
                            }
                        """)

                        cards.append({
                            "element": element,
                            "box": box,
                            "element_id": element_id,
                            "tag_name": tag_name,
                            "scroll_info": scroll_info
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

        # 检测卡片内部滚动条（内容溢出卡片容器）
        for card in cards:
            scroll_info = card.get("scroll_info", {})
            element_id = card.get("element_id", "未命名元素")

            # 检测垂直滚动条
            if scroll_info.get("hasVerticalScroll", False):
                vertical_overflow = scroll_info.get("verticalOverflow", 0)
                issues.append({
                    "type": "C",
                    "category": "inner_scroll_vertical",
                    "severity": "high",
                    "description": f"卡片内部垂直滚动条: 内容溢出 {vertical_overflow:.0f}px，需要滚动查看",
                    "details": {
                        "card_top": card["box"]["y"],
                        "card_height": card["box"]["height"],
                        "scroll_height": scroll_info.get("scrollHeight", 0),
                        "client_height": scroll_info.get("clientHeight", 0),
                        "overflow": vertical_overflow,
                        "element_id": element_id,
                        "position": f"({card['box']['x']:.0f}, {card['box']['y']:.0f})",
                    },
                })

            # 检测水平滚动条
            if scroll_info.get("hasHorizontalScroll", False):
                horizontal_overflow = scroll_info.get("horizontalOverflow", 0)
                issues.append({
                    "type": "D",
                    "category": "inner_scroll_horizontal",
                    "severity": "high",
                    "description": f"卡片内部水平滚动条: 内容溢出 {horizontal_overflow:.0f}px，需要滚动查看",
                    "details": {
                        "card_left": card["box"]["x"],
                        "card_width": card["box"]["width"],
                        "scroll_width": scroll_info.get("scrollWidth", 0),
                        "client_width": scroll_info.get("clientWidth", 0),
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
        print("✅ 正常 - 未发现内容问题")
        return "ok"

    # 统计不同类型的问题
    overflow_count = sum(1 for i in issues if i["category"] == "content_overflow")
    overlap_count = sum(1 for i in issues if i["category"] == "card_overlap")
    inner_scroll_v_count = sum(1 for i in issues if i["category"] == "inner_scroll_vertical")
    inner_scroll_h_count = sum(1 for i in issues if i["category"] == "inner_scroll_horizontal")

    print(f"⚠️  发现 {len(issues)} 个问题:")
    if overflow_count > 0:
        print(f"  - 内容溢出幻灯片: {overflow_count} 个")
    if overlap_count > 0:
        print(f"  - 卡片重叠: {overlap_count} 个")
    if inner_scroll_v_count > 0:
        print(f"  - 卡片内部垂直滚动: {inner_scroll_v_count} 个")
    if inner_scroll_h_count > 0:
        print(f"  - 卡片内部水平滚动: {inner_scroll_h_count} 个")
    print()

    for i, issue in enumerate(issues, 1):
        # 根据问题类型选择图标
        if issue["category"] == "card_overlap":
            issue_type = "📌"
        elif issue["category"] == "inner_scroll_vertical":
            issue_type = "📜⬇️"
        elif issue["category"] == "inner_scroll_horizontal":
            issue_type = "📜➡️"
        else:
            issue_type = "⬇️"

        print(f"  {issue_type} {i}. {issue['description']}")
        details = issue.get("details", {})

        # 显示元素标识信息（适用于所有类型）
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
        elif issue["category"] == "inner_scroll_vertical":
            print(f"      卡片尺寸: 可视高度={details['client_height']:.0f}px")
            print(f"      内容高度: {details['scroll_height']:.0f}px > 可视高度")
            print(f"      溢出量: {details['overflow']:.0f}px")
        elif issue["category"] == "inner_scroll_horizontal":
            print(f"      卡片尺寸: 可视宽度={details['client_width']:.0f}px")
            print(f"      内容宽度: {details['scroll_width']:.0f}px > 可视宽度")
            print(f"      溢出量: {details['overflow']:.0f}px")
        print()

    # 返回状态
    has_high = any(issue["severity"] == "high" for issue in issues)
    return "error" if has_high else "warning"


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="PPT 页面验证器 - 使用 Playwright 检测 PPT 中的内容溢出和卡片重叠问题",
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
  - 卡片内部垂直滚动条 (内容超出卡片高度)
  - 卡片内部水平滚动条 (内容超出卡片宽度)

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
            "inner_scroll_vertical": 0,
            "inner_scroll_horizontal": 0,
        }
    }

    for html_file in html_files:
        issues = check_scroll_with_playwright(str(html_file))
        status = print_single_file_result(html_file, issues)

        # 统计
        if status == "ok":
            summary["ok_files"] += 1
        elif status == "warning":
            summary["warning_files"] += 1
        else:
            summary["error_files"] += 1

        summary["total_issues"] += len(issues)
        for issue in issues:
            cat = issue.get("category", "")
            if cat in summary["issues_by_category"]:
                summary["issues_by_category"][cat] += 1

        all_results.append({
            "file": str(html_file),
            "status": status,
            "issue_count": len(issues),
            "issues": issues
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
    if summary["issues_by_category"]["inner_scroll_vertical"] > 0:
        print(f"    - 卡片内部垂直滚动: {summary['issues_by_category']['inner_scroll_vertical']}")
    if summary["issues_by_category"]["inner_scroll_horizontal"] > 0:
        print(f"    - 卡片内部水平滚动: {summary['issues_by_category']['inner_scroll_horizontal']}")

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
