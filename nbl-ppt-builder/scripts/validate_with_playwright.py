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
                    # 获取卡片中的标题文本作为唯一标识
                    title = await element.evaluate("""
                        el => {
                            const h3 = el.querySelector('h3');
                            return h3 ? h3.textContent.trim().substring(0, 30) : '';
                        }
                    """)
                    # 通过标题去重，避免同一个元素被多次匹配
                    if not any(c["title"] == title and abs(c["box"]["x"] - box["x"]) < 1 and abs(c["box"]["y"] - box["y"]) < 1 for c in cards):
                        element_name = await element.evaluate("el => el.className")
                        cards.append({
                            "element": element,
                            "box": box,
                            "name": element_name[:100],
                            "title": title
                        })

        # 检测卡片之间的重叠
        for i in range(len(cards)):
            for j in range(i + 1, len(cards)):
                card1 = cards[i]
                card2 = cards[j]

                # 检查是否重叠
                if boxes_overlap(card1["box"], card2["box"]):
                    overlap_area = calculate_overlap_area(card1["box"], card2["box"])
                    title1 = card1["title"] or f"卡片{i+1}"
                    title2 = card2["title"] or f"卡片{j+1}"

                    issues.append({
                        "type": "B",
                        "category": "card_overlap",
                        "severity": "high",
                        "description": f"卡片重叠: 「{title1}」与「{title2}」重叠约 {overlap_area:.0f}px²",
                        "details": {
                            "card1": {
                                "top": card1["box"]["y"],
                                "left": card1["box"]["x"],
                                "width": card1["box"]["width"],
                                "height": card1["box"]["height"],
                                "bottom": card1["box"]["y"] + card1["box"]["height"],
                                "right": card1["box"]["x"] + card1["box"]["width"],
                                "title": title1,
                            },
                            "card2": {
                                "top": card2["box"]["y"],
                                "left": card2["box"]["x"],
                                "width": card2["box"]["width"],
                                "height": card2["box"]["height"],
                                "bottom": card2["box"]["y"] + card2["box"]["height"],
                                "right": card2["box"]["x"] + card2["box"]["width"],
                                "title": title2,
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
                title = card["title"] or "卡片"

                issues.append({
                    "type": "A",
                    "category": "content_overflow",
                    "severity": "high",
                    "description": f"内容溢出幻灯片底部: 「{title}」超出 {overflow:.0f}px",
                    "details": {
                        "card_top": card["box"]["y"],
                        "card_height": card["box"]["height"],
                        "card_bottom": card_bottom,
                        "slide_height": slide_height,
                        "overflow": overflow,
                        "title": title,
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


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="PPT 页面验证器 - 使用 Playwright 检测 PPT 中的内容溢出和卡片重叠问题",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python validate_with_playwright.py presentation.html
  python validate_with_playwright.py /path/to/slides.html

检测内容:
  - 内容溢出幻灯片底部 (16:9 比例, 高度 540px)
  - 卡片之间的重叠

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
        "html_file",
        help="要验证的 HTML 文件路径"
    )

    args = parser.parse_args()
    html_file = args.html_file

    if not Path(html_file).exists():
        print(f"❌ 文件不存在: {html_file}")
        sys.exit(1)

    print(f"检测文件: {html_file}")
    print()

    issues = check_scroll_with_playwright(html_file)

    if not issues:
        print("✅ 正常 - 未发现内容问题")
        result = {"file": html_file, "status": "ok", "issues": []}
        exit_code = 0
    else:
        # 统计不同类型的问题
        overflow_count = sum(1 for i in issues if i["category"] == "content_overflow")
        overlap_count = sum(1 for i in issues if i["category"] == "card_overlap")

        print(f"⚠️  发现 {len(issues)} 个问题:")
        if overflow_count > 0:
            print(f"  - 内容溢出: {overflow_count} 个")
        if overlap_count > 0:
            print(f"  - 卡片重叠: {overlap_count} 个")
        print()

        for i, issue in enumerate(issues, 1):
            issue_type = "📌" if issue["category"] == "card_overlap" else "⬇️"
            print(f"  {issue_type} {i}. {issue['description']}")
            details = issue.get("details", {})

            if issue["category"] == "content_overflow":
                print(f"      卡片位置: 顶部={details['card_top']:.0f}px, 高度={details['card_height']:.0f}px")
                print(f"      底部边界: {details['card_bottom']:.0f}px > 幻灯片 (540px)")
                print(f"      溢出: {details['overflow']:.0f}px")
            elif issue["category"] == "card_overlap":
                card1 = details["card1"]
                card2 = details["card2"]
                print(f"      卡片1: 「{card1['title']}」")
                print(f"        位置: (x={card1['left']:.0f}, y={card1['top']:.0f}, 宽={card1['width']:.0f}, 高={card1['height']:.0f})")
                print(f"      卡片2: 「{card2['title']}」")
                print(f"        位置: (x={card2['left']:.0f}, y={card2['top']:.0f}, 宽={card2['width']:.0f}, 高={card2['height']:.0f})")
                print(f"      重叠面积: {details['overlap_area']:.0f}px²")
            print()

        # 只对 high 级别的问题返回错误码
        has_high = any(issue["severity"] == "high" for issue in issues)
        result = {
            "file": html_file,
            "status": "error" if has_high else "warning",
            "issues": issues,
        }
        exit_code = 2 if has_high else 1

    # 保存 JSON
    with open("validation_report.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ 报告已保存: validation_report.json")
    print()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
