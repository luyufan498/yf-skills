#!/usr/bin/env python3
"""cls_cdp.py — 财联社电报 CDP 提取（绕过 API sign 风控）

财联社电报页公开可读，页面 JS 自带签名渲染数据。
用 CDP 打开电报页 → 提取电报条目（时间+内容）→ 输出 JSON。

用法: python3 cls_cdp.py [--limit 20] [--json]
依赖: websocket-client（Hermes venv 已装）+ Chrome 9222（login-refresh 保证常驻）
"""
import argparse
import json
import sys
import time
import urllib.request

import websocket

CDP_HTTP = "http://127.0.0.1:9222"
PAGE_URL = "https://www.cls.cn/telegraph"
# 电报条目容器（时间+内容块）
ITEM_SEL = "div.p-t-20.p-b-20.b-b-w-1.b-b-s-s.b-c-e6e7ea"


def http_json(url: str, method: str = "GET") -> dict:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def extract(limit: int) -> list[dict]:
    tab = http_json(f"{CDP_HTTP}/json/new?{PAGE_URL}", method="PUT")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=40)

    def cdp(method: str, params: dict | None = None) -> dict:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == 1:
                return r

    try:
        time.sleep(8)  # SPA 渲染等待
        res = cdp("Runtime.evaluate", {
            "expression": f"""
            (() => {{
              const items = [...document.querySelectorAll('{ITEM_SEL}')].slice(0, {limit});
              return JSON.stringify(items.map(el => {{
                const t = el.innerText.trim();
                const m = t.match(/^(\\d{{2}}:\\d{{2}}:\\d{{2}})(.*)$/s);
                return m ? {{ time: m[1], content: m[2].trim().split('\\n')[0] }} : null;
              }}).filter(Boolean));
            }})()
            """,
            "returnByValue": True,
        })
        val = res.get("result", {}).get("result", {}).get("value", "[]")
        return json.loads(val)
    finally:
        ws.close()
        try:
            http_json(f"{CDP_HTTP}/json/close/{tab['id']}")
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    args = ap.parse_args()

    items = extract(args.limit)
    if args.json:
        print(json.dumps(items, ensure_ascii=False))
    else:
        for it in items:
            print(f"[{it['time']}] {it['content'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
