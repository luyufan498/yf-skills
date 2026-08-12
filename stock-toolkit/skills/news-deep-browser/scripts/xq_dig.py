#!/usr/bin/env python3
"""雪球按股一键深挖：开页 → 过滑块 → 读讨论 → 翻页 → 切资讯 → 输出结构化 JSON。

用法：
  python3 xq_dig.py <code> [--pages N] [--no-news]

env：
  DIVE_SESSION_FILE  存在时新开的 tab id 会记录进去，供 `cdp_drive.py clean` 关闭。

输出 JSON：{code, url, captcha:{triggered,solved}, pages:[{page,url,title,count,posts}], news:{...}}
"""
import argparse
import json
import sys
import time
import urllib.request

import cdp_drive as cd

WAIT_AFTER_LOAD = 3.0   # SPA 渲染缓冲
WAIT_AFTER_PAGE = 3.0   # 翻页后缓冲


def open_tab(url):
    t = cd.http_put(f"/json/new?{urllib.request.quote(url, safe='')}")
    cd.session_record(t["id"])
    return t["id"]


def wait_ready(ws, timeout=30):
    """轮询 document.readyState == complete，再留 SPA 渲染缓冲。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cd.eval_in(ws, "document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(WAIT_AFTER_LOAD)


def solve_captcha_if_needed(ws):
    """探测滑块，有则拖到底（最多 2 次）。返回 captcha 状态 dict。"""
    cap = {"triggered": False}
    try:
        has = cd.eval_in(ws, "!!document.querySelector('#aliyunCaptcha-sliding-body')")
    except Exception as e:
        cap["error"] = str(e)
        return cap
    if not has:
        return cap
    cap["triggered"] = True
    for attempt in range(2):
        res = cd.solve_captcha(ws)
        if res.get("solved"):
            cap.update({"solved": True, "attempts": attempt + 1})
            return cap
        time.sleep(2)
    cap.update({"solved": False, "attempts": 2})
    return cap


def read_discussion(ws, page):
    data = cd.eval_in(ws, cd.reader_js("xueqiu-discussion")) or {}
    return {"page": page, "url": data.get("url", ""), "title": data.get("title", ""),
            "count": data.get("count", 0), "posts": data.get("posts", [])}


def read_news(ws):
    clicked = cd.eval_in(ws, cd.click_js("text:资讯"))
    if not (clicked and clicked.get("ok")):
        return {"click_failed": clicked}
    time.sleep(WAIT_AFTER_PAGE)
    data = cd.eval_in(ws, cd.reader_js("xueqiu-news")) or {}
    return {"url": data.get("url", ""), "title": data.get("title", ""),
            "count": data.get("count", 0), "posts": data.get("posts", [])}


def main():
    ap = argparse.ArgumentParser(description="雪球按股一键深挖")
    ap.add_argument("code", help="股票代码，如 SH600519 / SZ002837")
    ap.add_argument("--pages", type=int, default=1, help="讨论区翻页到第几页（默认 1）")
    ap.add_argument("--no-news", action="store_true", help="跳过资讯 tab")
    args = ap.parse_args()

    code = args.code.upper().replace(".", "").replace("_", "")
    url = f"https://xueqiu.com/S/{code}"
    result = {"code": code, "url": url, "captcha": {}, "pages": [], "news": None, "ok": True}

    try:
        tid = open_tab(url)
    except Exception as e:
        result.update({"ok": False, "error": f"{e}",
                       "hint": "Chrome 9222 未启动？请先以 --remote-debugging-port=9222 启动 Chrome"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    ws = cd.find_target(tid)["webSocketDebuggerUrl"]

    wait_ready(ws)
    result["captcha"] = solve_captcha_if_needed(ws)
    result["pages"].append(read_discussion(ws, 1))

    for page in range(2, args.pages + 1):
        try:
            cd.eval_in(ws, cd.paginate_js(page))
            time.sleep(WAIT_AFTER_PAGE)
            result["pages"].append(read_discussion(ws, page))
        except Exception as e:
            result["pages"].append({"page": page, "error": str(e)})

    if not args.no_news:
        result["news"] = read_news(ws)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
