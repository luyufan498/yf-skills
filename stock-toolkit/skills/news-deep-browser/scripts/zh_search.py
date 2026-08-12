#!/usr/bin/env python3
"""知乎搜索+读结果（搜索页不触发 40362，可作问题页的兜底渠道）。

用法：
  python3 zh_search.py <关键词>

env：
  DIVE_SESSION_FILE  存在时新开的 tab id 会记录进去，供 `cdp_drive.py clean` 关闭。

输出 JSON：{ok, query, url, title, count, results:[{text}]}
"""
import argparse
import json
import sys
import time
import urllib.request

import cdp_drive as cd

WAIT_AFTER_LOAD = 3.0  # SPA 渲染缓冲


def main():
    ap = argparse.ArgumentParser(description="知乎搜索")
    ap.add_argument("query", help="搜索关键词，如 预计赛力斯")
    args = ap.parse_args()

    url = f"https://www.zhihu.com/search?q={urllib.request.quote(args.query)}&type=content"
    try:
        t = cd.http_put(f"/json/new?{urllib.request.quote(url, safe='')}")
        tid = t["id"]
        cd.session_record(tid)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{e}",
                          "hint": "Chrome 9222 未启动？请先以 --remote-debugging-port=9222 启动 Chrome"},
                         ensure_ascii=False))
        sys.exit(1)

    ws = cd.find_target(tid)["webSocketDebuggerUrl"]
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if cd.eval_in(ws, "document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(WAIT_AFTER_LOAD)

    data = cd.eval_in(ws, cd.reader_js("zhihu-search")) or {}
    result = {"ok": True, "query": args.query, "url": data.get("url", ""),
              "title": data.get("title", ""), "count": data.get("count", 0),
              "results": data.get("results", [])}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
