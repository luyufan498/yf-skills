#!/usr/bin/env python3
"""X 推荐流扫描：For you 首页（已关注账号+算法推荐）+ Explore 趋势（全球热点）。

覆盖主动搜索想不到的内容，与 x_search.py 配对用（先推荐后搜索）。

用法：
  python3 x_scan.py [--home | --explore | --both]   # 默认 --both

env：
  DIVE_SESSION_FILE  存在时新开的 tab id 会记录进去，供 `cdp_drive.py clean` 关闭。

输出 JSON：
  {home: {count, tweets:[{author, text, url, time}]},
   explore: {trends:[...], tweets:[...]}}
"""
import argparse
import json
import sys
import time
import urllib.request

import cdp_drive as cd

WAIT_AFTER_LOAD = 8.0  # X 是重型 SPA，等渲染


def open_tab(url):
    t = cd.http_put(f"/json/new?{urllib.request.quote(url, safe='')}")
    cd.session_record(t["id"])
    return t["id"]


def wait_ready(ws, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cd.eval_in(ws, "document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(WAIT_AFTER_LOAD)


def scan(url, sites):
    try:
        tid = open_tab(url)
    except Exception as e:
        return {"ok": False, "error": f"{e}",
                "hint": "Chrome 9222 未启动？请先以 --remote-debugging-port=9222 启动 Chrome"}
    ws = cd.find_target(tid)["webSocketDebuggerUrl"]
    wait_ready(ws)
    data = {}
    for site in sites:
        try:
            r = cd.eval_in(ws, cd.reader_js(site)) or {}
            data[site] = r
        except Exception as e:
            data[site] = {"error": str(e)}
    data["ok"] = True
    return data


def main():
    ap = argparse.ArgumentParser(description="X 推荐流扫描")
    ap.add_argument("--home", action="store_true", help="只扫 For you 首页")
    ap.add_argument("--explore", action="store_true", help="只扫 Explore 趋势")
    args = ap.parse_args()
    both = not args.home and not args.explore
    result = {}

    if args.home or both:
        result["home"] = scan("https://x.com/home", ["x-timeline"])
    if args.explore or both:
        # 趋势 + 趋势时间线都抓
        result["explore"] = scan("https://x.com/explore", ["x-trends", "x-timeline"])

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
