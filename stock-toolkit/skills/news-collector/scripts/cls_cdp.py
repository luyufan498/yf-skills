#!/usr/bin/env python3
"""cls_cdp.py — 财联社电报 CDP 提取（绕过 API sign 风控）

财联社电报页公开可读，页面 JS 自带签名渲染数据。
用 CDP 打开电报页 → 提取电报条目（时间+内容+链接）；--full 时对带 /detail/ 的
完整报道自动进详情页抓全文。

用法:
  python3 cls_cdp.py --limit 20          # 列表（摘要 + href）
  python3 cls_cdp.py --limit 10 --full   # 列表 + 重要报道全文
  python3 cls_cdp.py --detail 2453431    # 指定详情页全文
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
ITEM_SEL = "div.p-t-20.p-b-20.b-b-w-1.b-b-s-s.b-c-e6e7ea"


def http_json(url: str, method: str = "GET") -> dict:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _cdp_eval(ws, expression: str):
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 1:
            return r.get("result", {}).get("result", {}).get("value", "[]")


def open_tab(url: str):
    tab = http_json(f"{CDP_HTTP}/json/new?{url}", method="PUT")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=40)
    return tab, ws


def close_tab(tab):
    ws_cleanup = None
    try:
        http_json(f"{CDP_HTTP}/json/close/{tab['id']}")
    except Exception:
        pass


def list_telegraph(limit: int) -> list[dict]:
    tab, ws = open_tab(PAGE_URL)
    try:
        time.sleep(8)
        val = _cdp_eval(ws, f"""
        (() => {{
          const items = [...document.querySelectorAll('{ITEM_SEL}')].slice(0, {limit});
          return JSON.stringify(items.map(el => {{
            const t = el.innerText.trim();
            const m = t.match(/^(\\d{{2}}:\\d{{2}}:\\d{{2}})(.*)$/s);
            const a = el.querySelector('a');
            return m ? {{
              time: m[1],
              content: m[2].trim().split('\\n')[0],
              href: a ? a.getAttribute('href') : null
            }} : null;
          }}).filter(Boolean));
        }})()
        """)
        return json.loads(val)
    finally:
        ws.close()
        close_tab(tab)


def fetch_detail(detail_id: str) -> dict:
    tab, ws = open_tab(f"https://www.cls.cn/detail/{detail_id}")
    try:
        time.sleep(6)
        val = _cdp_eval(ws, """JSON.stringify({
          title: document.title,
          body: (document.body ? document.body.innerText : '').split('收藏')[0].split('我要评论')[0]
        })""")
        d = json.loads(val)
        # 去掉导航噪音：取标题之后的内容
        txt = d.get("body", "")
        idx = txt.find(d.get("title", "财联社"))
        if idx > 0 and idx < len(txt):
            txt = txt[idx:]
        return {"detail_id": detail_id, "title": d.get("title", ""), "full": txt.strip()[:1000]}
    finally:
        ws.close()
        close_tab(tab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--full", action="store_true", help="对带 /detail/ 的报道自动抓全文")
    ap.add_argument("--detail", type=str, help="指定详情页 ID 抓全文")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.detail:
        d = fetch_detail(args.detail)
        print(json.dumps(d, ensure_ascii=False) if args.json else f"# {d['title']}\n{d['full']}")
        return 0

    items = list_telegraph(args.limit)
    if args.full:
        for it in items:
            if it.get("href") and "/detail/" in it["href"]:
                did = it["href"].rsplit("/", 1)[-1]
                try:
                    d = fetch_detail(did)
                    it["full"] = d["full"]
                except Exception:
                    it["full"] = None

    if args.json:
        print(json.dumps(items, ensure_ascii=False))
    else:
        for it in items:
            extra = " 📄全文" if it.get("full") else ""
            print(f"[{it['time']}] {it['content'][:80]}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
