#!/usr/bin/env python3
"""
PPT 页面验证器 - 使用 Playwright 真实检测滚动条和边界溢出 + CSS语法验证
"""

import argparse
import asyncio
import sys
import json
import re
from pathlib import Path
from urllib.parse import quote

from css_validator import CSSValidator


def check_scroll_with_playwright(html_file):
    """使用 Playwright 真实检测滚动条（保持向后兼容的同步接口）"""
    return asyncio.run(_check_scroll_with_playwright_async_wrapper(html_file))


async def _check_scroll_with_playwright_async_wrapper(html_file):
    """单文件包装器：使用共享 browser 池检测一个文件"""
    async with _get_browser_pool() as (browser, _):
        return await detect_with_playwright_async(html_file, browser)


async def detect_with_playwright_async(html_file, browser=None):
    """使用 Playwright 检测内容是否溢出幻灯片底部

    Args:
        html_file: HTML 文件路径
        browser: 可选，外部传入的复用 browser。若为 None 则自启 browser（兼容旧行为）
    """
    from playwright.async_api import async_playwright

    issues = []
    own_browser = browser is None
    if own_browser:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)

    try:
        page = await browser.new_page(viewport={"width": 960, "height": 540})
        file_url = f"file://{quote(str(Path(html_file).absolute()))}"
        await page.goto(file_url, wait_until="networkidle", timeout=30000)

        # 等待 .slide-container 就绪（再兜底一个短超时）
        try:
            await page.wait_for_selector(".slide-container", timeout=5000)
        except Exception:
            # 没有 .slide-container 的页面，继续走原逻辑（返回空 issues）
            await page.close()
            return []

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
            await page.close()
            return []

        # 查找所有绝对定位的直接子元素
        absolute_elements = await page.evaluate("""
            () => {
                const container = document.querySelector('.slide-container');
                if (!container) return [];

                const children = Array.from(container.children);
                const absoluteDivs = children.filter(el => {
                    if (el.tagName.toLowerCase() !== 'div') return false;

                    const style = window.getComputedStyle(el);
                    if (style.position !== 'absolute') return false;

                    // 排除显式标记为装饰性的元素（data-decorative 或 .decorative 类）
                    if (el.hasAttribute('data-decorative') || el.classList.contains('decorative')) return false;

                    // 排除装饰性元素（pointer-events: none 或完全透明）
                    if (style.pointerEvents === 'none' || style.opacity === '0') return false;

                    // 排除包含 "pointer-events-none" 类的元素
                    if (el.classList.contains('pointer-events-none')) return false;

                    // 排除纯装饰性的分隔线（高度或宽度很小）
                    const rect = el.getBoundingClientRect();
                    if (rect.height < 3 || rect.width < 3) return false;

                    // 排除纯装饰性的背景块（没有文字内容的背景色块）
                    const textContent = el.textContent.trim();
                    const hasText = textContent.length > 0;
                    const hasBackground = style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
                                         style.backgroundColor !== 'transparent';
                    const hasChildren = el.children.length > 0;

                    if (!hasText && !hasChildren && hasBackground) {
                        return false;
                    }

                    // 排除只包含图片的装饰性元素
                    if (hasChildren && !hasText) {
                        const childElements = Array.from(el.children);
                        const onlyHasImages = childElements.every(child =>
                            child.tagName.toLowerCase() === 'img' ||
                            (child.tagName.toLowerCase() === 'div' && child.children.length === 0)
                        );
                        if (onlyHasImages) {
                            return false;
                        }
                    }

                    // 排除页码元素：优先识别带 page-number / pagination 类的元素
                    if (el.classList.contains('page-number') || el.classList.contains('pagination')) {
                        return false;
                    }
                    // 兜底启发式：右下角小区域且只含数字
                    if (rect.height < 30 && rect.width < 50) {
                        const text = el.textContent.trim();
                        if (/^[\\d\\s]+$/.test(text) && text.length < 10) {
                            return false;
                        }
                    }

                    return true;
                });

                return absoluteDivs.map((el) => {
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

        cards = []
        for elem_info in absolute_elements:
            index = elem_info["index"]
            element_id = elem_info["elementId"]
            class_name = elem_info["className"]

            element = await page.query_selector(f".slide-container > div:nth-child({index + 1})")
            if not element:
                continue

            box = await element.bounding_box()
            if not box:
                continue

            relative_box = {
                "x": box["x"] - container_box["x"],
                "y": box["y"] - container_box["y"],
                "width": box["width"],
                "height": box["height"]
            }

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
                "box": relative_box,
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

        # 检测内容溢出幻灯片底部：使用容器实际高度而非硬编码 540
        slide_height = container_box["height"]
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

        await page.close()
        return issues
    except Exception as e:
        # 单文件失败不应中断批量检测
        print(f"  ⚠️  检测失败 {Path(html_file).name}: {e}")
        return []
    finally:
        if own_browser:
            await browser.close()
            await pw.stop()


# 浏览器池：批量检测时复用同一实例
_browser_pool_state = {"pw": None, "browser": None, "refcount": 0}


class _BrowserPoolContext:
    """异步上下文管理器：首次进入时启动 browser，最后一次退出时关闭"""
    async def __aenter__(self):
        if _browser_pool_state["browser"] is None:
            from playwright.async_api import async_playwright
            _browser_pool_state["pw"] = await async_playwright().start()
            _browser_pool_state["browser"] = await _browser_pool_state["pw"].chromium.launch(headless=True)
        _browser_pool_state["refcount"] += 1
        return _browser_pool_state["browser"], _browser_pool_state

    async def __aexit__(self, exc_type, exc, tb):
        _browser_pool_state["refcount"] -= 1
        if _browser_pool_state["refcount"] <= 0 and _browser_pool_state["browser"] is not None:
            await _browser_pool_state["browser"].close()
            await _browser_pool_state["pw"].stop()
            _browser_pool_state["browser"] = None
            _browser_pool_state["pw"] = None
            _browser_pool_state["refcount"] = 0
        return False


def _get_browser_pool():
    """获取浏览器池上下文管理器"""
    return _BrowserPoolContext()


def boxes_overlap(box1, box2):
    """检查两个矩形是否重叠"""
    x1_left = box1["x"]
    x1_right = box1["x"] + box1["width"]
    y1_top = box1["y"]
    y1_bottom = box1["y"] + box1["height"]

    x2_left = box2["x"]
    x2_right = box2["x"] + box2["width"]
    y2_top = box2["y"]
    y2_bottom = box2["y"] + box2["height"]

    overlap_x = x1_right > x2_left and x2_right > x1_left
    overlap_y = y1_bottom > y2_top and y2_bottom > y1_top

    return overlap_x and overlap_y


def calculate_overlap_area(box1, box2):
    """计算两个矩形的重叠面积"""
    x_overlap_left = max(box1["x"], box2["x"])
    x_overlap_right = min(box1["x"] + box1["width"], box2["x"] + box2["width"])
    y_overlap_top = max(box1["y"], box2["y"])
    y_overlap_bottom = min(box1["y"] + box1["height"], box2["y"] + box2["height"])

    overlap_width = max(0, x_overlap_right - x_overlap_left)
    overlap_height = max(0, y_overlap_bottom - y_overlap_top)

    return overlap_width * overlap_height


def collect_html_files(paths):
    """收集所有要检测的 HTML 文件"""
    html_files = []

    for path in paths:
        p = Path(path)

        if p.is_file():
            if p.suffix.lower() == '.html':
                html_files.append(p)
        elif p.is_dir():
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

        # CSS语法错误的特殊处理（新 CSSValidator 返回 {line, column, type, message, suggestion, context}）
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
            print(f"      底部边界: {details['card_bottom']:.0f}px > 幻灯片 ({details['slide_height']:.0f}px)")
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


async def _validate_all_files_async(html_files, css_validator):
    """并发检测所有文件：复用 browser 池 + asyncio.gather"""
    async with _get_browser_pool() as (browser, _):
        async def validate_one(html_file):
            layout_issues = await detect_with_playwright_async(str(html_file), browser)
            css_issues = css_validator.validate_html_file(str(html_file))
            # 兼容新 CSSValidator 返回结构 → 转换为统一的 issue 格式
            normalized_css_issues = []
            for e in css_issues:
                normalized_css_issues.append({
                    "type": "E",
                    "category": "css_syntax_error",
                    "severity": "high",
                    "description": e["message"],
                    "details": {
                        "line": e.get("line", 0),
                        "context": e.get("context", ""),
                        "suggestion": e.get("suggestion", ""),
                    },
                })
            return str(html_file), layout_issues + normalized_css_issues

        # 限制并发数避免内存爆炸（每个 page 约占 100MB）
        sem = asyncio.Semaphore(4)

        async def validate_with_sem(html_file):
            async with sem:
                return await validate_one(html_file)

        return await asyncio.gather(*(validate_with_sem(f) for f in html_files))


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
  - 内容溢出幻灯片底部 (16:9 比例)
  - 卡片之间的重叠
  - 卡片内部内容垂直溢出 (内容超出卡片高度)
  - 卡片内部内容水平溢出 (内容超出卡片宽度)
  - CSS语法错误 (Tailwind类名误用在style属性中)

输出:
  - 终端显示检测结果的详细信息
  - 生成 validation_report.json 文件 (包含所有问题的详细数据)
  - 退出码: 0=正常, 1=警告, 2=错误

环境要求:
  - Python 3.13+
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

    print(f"🔍 开始检测 {len(html_files)} 个文件（并发复用 browser）...")
    print()

    # 创建CSS验证器
    css_validator = CSSValidator()

    # 批量并发检测
    results = asyncio.run(_validate_all_files_async(html_files, css_validator))
    file_to_issues = {f: iss for f, iss in results}

    # 输出每个文件的结果
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

    for html_file in html_files:
        all_issues = file_to_issues.get(str(html_file), [])

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
