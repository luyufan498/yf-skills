#!/usr/bin/env python3
"""CDP 抓同花顺概念板块全量成分股（浏览器指纹绕开 API 反爬）。

东财 push2 API 对服务器 IP 风控（curl/浏览器 fetch 均被 RST），
同花顺概念页用真实 Chrome 可稳定抓取。

用法:
  python3 ths_cons_cdp.py <板块代码> [板块名] [页数]
  python3 ths_cons_cdp.py 309130 商业航天 5
  python3 ths_cons_cdp.py list   # 列出所有概念板块代码

依赖: Chrome 9222 常驻 + websocket-client
输出: JSON 到 stdout（{code, name} 列表）
"""
import json
import sys
import time
import urllib.request
import websocket

BASE = "http://127.0.0.1:9222"


def new_tab(url):
    req = urllib.request.Request(f"{BASE}/json/new?{url}", method="PUT")
    return json.loads(urllib.request.urlopen(req).read())


def list_boards():
    """列出所有概念板块（代码+名称），从概念列表页 DOM 抓。"""
    tab = new_tab("https://q.10jqka.com.cn/gn/")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
    state = {"msg_id": 0}

    def cmd(method, params=None):
        state["msg_id"] += 1
        mid = state["msg_id"]
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid:
                return r

    time.sleep(8)
    expr = """JSON.stringify([...document.querySelectorAll('a')]
      .filter(a => /code\\/\\d+/.test(a.href) && a.textContent.trim().length > 0 && a.textContent.trim().length < 12)
      .map(a => a.href.match(/code\\/(\\d+)/)[1] + ' ' + a.textContent.trim()))"""
    r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    cmd("Page.close")
    val = r["result"]["result"]["value"]
    rows = json.loads(val) if val else []
    # 去重保序
    seen, out = set(), []
    for x in rows:
        if x not in seen:
            seen.add(x)
            out.append(x)
    for x in out:
        print(x)
    return out


def fetch(code, label, pages=3):
    """抓板块成分（翻 N 页，按涨幅排序的成分列表页）。"""
    tab = new_tab(f"https://q.10jqka.com.cn/gn/detail/code/{code}/")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
    state = {"msg_id": 0}

    def cmd(method, params=None):
        state["msg_id"] += 1
        mid = state["msg_id"]
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid:
                return r

    all_rows = []
    for page in range(1, pages + 1):
        if page > 1:
            cmd("Runtime.evaluate", {
                "expression": f"location.href='https://q.10jqka.com.cn/gn/detail/field/264648/order/desc/page/{page}/ajax/1/code/{code}'"})
            time.sleep(4)
        else:
            time.sleep(8)
        expr = """JSON.stringify([...document.querySelectorAll('.m-table tbody tr')].map(tr => {
          const tds = [...tr.querySelectorAll('td')];
          return {code: (tds[1]?.textContent||'').trim(), name: (tds[2]?.textContent||'').trim()};
        }).filter(x => x.code && x.code.length === 6))"""
        r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        val = r["result"]["result"]["value"]
        if val:
            rows = json.loads(val)
            all_rows.extend(rows)
            sys.stderr.write(f"  第{page}页: {len(rows)} 只\n")
        time.sleep(2)
    cmd("Page.close")
    seen, uniq = set(), []
    for x in all_rows:
        if x["code"] not in seen:
            seen.add(x["code"])
            uniq.append(x)
    print(json.dumps(uniq, ensure_ascii=False))
    sys.stderr.write(f"=== {label} 合计 {len(uniq)} 只 ===\n")
    return uniq


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "list":
        list_boards()
    else:
        code = sys.argv[1]
        label = sys.argv[2] if len(sys.argv) > 2 else code
        pages = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        fetch(code, label, pages)
