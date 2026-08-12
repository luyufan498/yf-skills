#!/usr/bin/env python3
"""X 搜索：定向搜 AI/芯片/HBM/大模型等关键词（国际科技情报）。

用法：
  python3 x_search.py <query> [--f top|live] [--since DATE] [--min-faves N] [--max N]
  例：python3 x_search.py "AI chip" --f live --since 2026-08-09 --min-faves 50

env：
  DIVE_SESSION_FILE  存在时新开的 tab id 会记录进去，供 `cdp_drive.py clean` 关闭。

输出 JSON：
  {ok, query, f, url, count, tweets:[{author, text, url, time}]}
"""
import argparse
import json
import sys
import time
import urllib.request

import cdp_drive as cd

WAIT_AFTER_LOAD = 4.0


def main():
    ap = argparse.ArgumentParser(description="X 搜索")
    ap.add_argument("query", help="搜索词，如 \"AI chip\" / HBM / 大模型")
    ap.add_argument("--f", default="top", choices=["top", "live"], help="top=热门 live=最新")
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--min-faves", type=int, default=0, help="最低点赞数过滤")
    ap.add_argument("--max", type=int, default=20, help="最多返回条数")
    args = ap.parse_args()

    parts = [args.query]
    if args.since:
        parts.append(f"since:{args.since}")
    if args.min_faves:
        parts.append(f"min_faves:{args.min_faves}")
    q = " ".join(parts)
    url = f"https://x.com/search?q={urllib.request.quote(q)}&f={args.f}"

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

    data = cd.eval_in(ws, cd.reader_js("x-timeline")) or {}
    tweets = data.get("tweets", [])[:args.max]
    result = {"ok": True, "query": q, "f": args.f, "url": data.get("url", ""),
              "count": len(tweets), "tweets": tweets}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
