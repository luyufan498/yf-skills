#!/usr/bin/env python3
"""Bare-CDP drive helper for deep-browser.
Stateless: each command opens a fresh WS connection, does its thing, closes.
No daemon, no agent-browser. Session state = the real Chrome tabs themselves.

Env:
  DIVE_SESSION_FILE   set 时 new 会把新 tab id 追加到该文件（每行一个），
                      供任务结束 / cron 兜底用 `clean` 关闭 dive 开的 tab。

Usage:
  python3 cdp_drive.py list
  python3 cdp_drive.py new <url>                       # open a new tab
  python3 cdp_drive.py close <target_id>               # close a tab
  python3 cdp_drive.py clean [--file PATH]             # close ALL tabs in session file (cron 兜底)
  python3 cdp_drive.py dedupe [--file PATH]            # keep ONE tab per site, close rest (任务收尾)
  python3 cdp_drive.py dedupe --all                    # 全部 tab 同站留 1（含手动开的重
                                                        # 复 tab，优先保活跃/手动 tab，保持 session）
  python3 cdp_drive.py navigate <target_id> <url>      # navigate an existing tab
  python3 cdp_drive.py eval <target_id> <js>
  python3 cdp_drive.py read <target_id> [--site SITE]
  python3 cdp_drive.py click <target_id> <selector> [--index n]
  python3 cdp_drive.py scroll <target_id> [down|up|<n>px]
  python3 cdp_drive.py wait <target_id> <selector> [--timeout ms]
  python3 cdp_drive.py paginate <target_id> [page_n]
  python3 cdp_drive.py captcha <target_id>
  python3 cdp_drive.py title <target_id>
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import websocket

CDP_BROWSER = "http://localhost:9222"
import json
import re
import sys
import time
import urllib.request
import websocket

CDP_BROWSER = "http://localhost:9222"


def http_get(path):
    with urllib.request.urlopen(CDP_BROWSER + path, timeout=5) as r:
        return json.loads(r.read().decode())


def http_put(path):
    req = urllib.request.Request(CDP_BROWSER + path, method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def http_close(tid):
    """关 tab：Chrome /json/close/{id} 是 GET（/json/new 才是 PUT）。"""
    with urllib.request.urlopen(CDP_BROWSER + f"/json/close/{tid}", timeout=10) as r:
        return r.read().decode()


def session_file_path():
    return os.environ.get("DIVE_SESSION_FILE")


def session_record(tid):
    """env DIVE_SESSION_FILE 存在时把 tid 追加进去（dive 开的 tab，供 clean 关闭）。"""
    path = session_file_path()
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(tid + "\n")
    except OSError as e:
        print(f"warn: 无法记录 session tab {tid} -> {path}: {e}", file=sys.stderr)


def session_read(path):
    """读 session 文件，返回去重后的 tab id 列表（容忍脏行/重复）。"""
    tids = []
    try:
        with open(path) as f:
            for line in f:
                t = line.strip()
                if t and t not in tids:
                    tids.append(t)
    except OSError:
        return []
    return tids


def find_target(tid):
    for t in http_get("/json"):
        if t["id"] == tid:
            return t
    raise SystemExit(f"target {tid} not found")


class Page:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, origin="http://localhost:9222")
        self.msg_id = 0

    def send(self, method, params=None):
        self.msg_id += 1
        mid = self.msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def eval_in(ws_url, js, await_promise=True):
    p = Page(ws_url)
    r = p.send("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": await_promise})
    p.close()
    if "error" in r:
        return {"error": r["error"]}
    res = r.get("result", {}).get("result", {})
    if res.get("type") == "undefined":
        return {"type": "undefined"}
    if res.get("subtype") == "error":
        return {"js_error": res.get("description")}
    return res.get("value")


def cmd_list():
    for t in http_get("/json"):
        if t.get("type") == "page":
            print(t["id"], t.get("url", "")[:90])


def cmd_new(url):
    t = http_put(f"/json/new?{urllib.request.quote(url, safe='')}")
    session_record(t["id"])
    print(t["id"], t.get("url", "")[:90])


def cmd_close(tid):
    try:
        r = http_close(tid)
        print(json.dumps({"closed": tid, "res": r}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"closed": tid, "error": str(e)}, ensure_ascii=False))


def cmd_clean(filepath):
    """关掉 session 文件里记录的所有 tab 并清空文件（cron 兜底用，全清）。"""
    tids = session_read(filepath)
    closed, gone = 0, 0
    for tid in tids:
        try:
            http_close(tid)
            closed += 1
        except Exception:
            gone += 1  # 已不在 tab 列表（可能已关）
    try:
        with open(filepath, "w") as f:
            f.truncate()
    except OSError:
        pass
    print(json.dumps({"clean": {"total": len(tids), "closed": closed, "already_gone": gone}}, ensure_ascii=False))


def main_domain(host):
    """提取主域名（netloc → example.com，忽略 www 和子域名）。"""
    parts = host.split(".")
    # 取最后两段即主域（xueqiu.com / zhihu.com / x.com）；www. 等子域自然并入
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def cmd_dedupe(filepath, all_tabs=False):
    """dive 开的 tab 每站最多留一个：按主域名分组，每组保留第一个，其余关掉。
    只处理 session 文件记录的 tab（= dive new 开的），用户手动开的 tab 不在此列。
    保留的 tab 从 session 移除（不再需要清理），并清空文件。

    all_tabs=True（dedupe --all）：对**全部** page tab 去重（任务结束清理用）——
    每站保留 1 个保持登录 session，优先保留活跃 tab > 手动开的 tab > dive 开的 tab。
    此时不要求 DIVE_SESSION_FILE，也不清空 session 文件。
    """
    if all_tabs:
        tabs = [t for t in http_get("/json") if t.get("type") == "page"]
        dive = set(session_read(filepath))
        by_domain = {}
        for t in tabs:
            host = urllib.parse.urlparse(t.get("url", "")).netloc
            by_domain.setdefault(main_domain(host), []).append(t)
        closed, keep = [], []
        for domain, ts in sorted(by_domain.items()):
            # 优先保留：活跃 tab → 非 dive(手动开) → dive 开
            ts.sort(key=lambda t: (not t.get("active", False), t["id"] in dive))
            for i, t in enumerate(ts):
                if i == 0:
                    keep.append({"tid": t["id"], "domain": domain})
                else:
                    try:
                        http_close(t["id"])
                    except Exception:
                        pass
                    closed.append({"tid": t["id"], "domain": domain})
        print(json.dumps({
            "dedupe_all": {"total": len(tabs), "closed": len(closed),
                           "kept": keep}
        }, ensure_ascii=False, indent=2))
        return
    tids = session_read(filepath)
    by_domain = {}  # domain -> [tids]
    gone = 0
    for tid in tids:
        try:
            t = find_target(tid)
            host = urllib.parse.urlparse(t.get("url", "")).netloc
            by_domain.setdefault(main_domain(host), []).append(tid)
        except Exception:
            gone += 1
    closed, keep = [], []
    for domain, ids in sorted(by_domain.items()):
        for i, tid in enumerate(ids):
            if i == 0:
                keep.append({"tid": tid, "domain": domain})
            else:
                try:
                    http_close(tid)
                except Exception:
                    pass
                closed.append({"tid": tid, "domain": domain})
    try:
        with open(filepath, "w") as f:
            f.truncate()
    except OSError:
        pass
    print(json.dumps({
        "dedupe": {"total": len(tids), "closed": len(closed), "kept": keep, "already_gone": gone}
    }, ensure_ascii=False, indent=2))


def wait_load(ws_url, timeout=45):
    ws = websocket.create_connection(ws_url, timeout=timeout, origin="http://localhost:9222")
    for method in ("Page.enable", "Runtime.enable"):
        ws.send(json.dumps({"id": 1 if method == "Page.enable" else 2, "method": method}))
    ws.settimeout(timeout)
    try:
        while True:
            msg = json.loads(ws.recv())
            m = msg.get("method")
            if m == "Page.loadEventFired":
                break
            if m == "Page.frameStoppedLoading":
                time.sleep(0.8)
                break
    except websocket.WebSocketTimeoutException:
        pass
    ws.close()


def cmd_navigate(tid, url):
    t = find_target(tid)
    p = Page(t["webSocketDebuggerUrl"])
    p.send("Page.enable")
    p.send("Runtime.enable")
    p.send("Page.navigate", {"url": url})
    # wait for load (async messages may interleave; Page.navigate has no direct ack)
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=45, origin="http://localhost:9222")
    ws.settimeout(45)
    try:
        while True:
            msg = json.loads(ws.recv())
            m = msg.get("method")
            if m == "Page.loadEventFired":
                break
            if m == "Page.frameStoppedLoading":
                time.sleep(0.8)
                break
    except websocket.WebSocketTimeoutException:
        pass
    ws.close()
    p.close()
    print("navigated", url)


def reader_js(site):
    """Return JS that extracts agent-readable content for a given site."""
    readers = {
        "generic": """(() => {
          const el = document.body;
          const text = el ? el.innerText : '';
          return {url: location.href, title: document.title, len: text.length, text: text.slice(0, 4000)};
        })()""",
        "xueqiu-discussion": """(() => {
          const items = [...document.querySelectorAll('.timeline__item')];
          return {
            url: location.href, title: document.title,
            count: items.length,
            posts: items.map(el => {
              const c = el.querySelector('.content');
              const d = el.querySelector('.date-and-source');
              const u = el.querySelector('.user-name') || el.querySelector('.name') || el.querySelector('a.user');
              return {
                user: u ? u.innerText.trim() : '',
                date: d ? d.innerText.trim() : '',
                text: c ? c.innerText.trim() : (el.innerText || '').trim()
              };
            }).filter(x => x.text)
          };
        })()""",
        "xueqiu-news": """(() => {
          const items = [...document.querySelectorAll('.news-article, .article, .feed__item, .timeline__item')];
          const text = document.body ? document.body.innerText : '';
          return {
            url: location.href, title: document.title,
            count: items.length,
            posts: items.map(el => ({
              text: (el.innerText || '').trim()
            })).filter(x => x.text).slice(0, 30),
            bodyPreview: text.slice(0, 2000)
          };
        })()""",
        "xueqiu-livenews": """(() => {
          const text = document.body ? document.body.innerText : '';
          return {url: location.href, title: document.title, len: text.length, text: text.slice(0, 4000)};
        })()""",
        "zhihu-search": """(() => {
          const items = [...document.querySelectorAll('.SearchResult-Card, .SearchItem, .Card.SearchResult-Card')];
          return {
            url: location.href, title: document.title,
            count: items.length,
            results: items.map(el => ({text: (el.innerText || '').trim()})).filter(x => x.text).slice(0, 20)
          };
        })()""",
        "x-timeline": """(() => {
          const arts = [...document.querySelectorAll('article[data-testid=tweet]')];
          return {
            url: location.href, title: document.title, count: arts.length,
            tweets: arts.slice(0, 30).map(a => {
              const t = a.querySelector('[data-testid=tweetText]');
              const un = a.querySelector('[data-testid=User-Name]');
              const link = a.querySelector('a[href*="/status/"]');
              const time = a.querySelector('time');
              return {
                author: un ? un.innerText.replace(/\\n/g, ' ').slice(0, 60) : '',
                text: t ? t.innerText.slice(0, 400) : '',
                url: link ? ('https://x.com' + link.getAttribute('href').split('?')[0]) : '',
                time: time ? time.getAttribute('datetime') : ''
              };
            }).filter(x => x.text)
          };
        })()""",
        "x-trends": """(() => {
          const trends = [...document.querySelectorAll('div[data-testid=trend]')]
            .map(t => t.innerText.replace(/\\n/g, ' · ').slice(0, 120));
          return {url: location.href, title: document.title, trends};
        })()""",
    }
    return readers.get(site, readers["generic"])


def cmd_read(tid, site="generic"):
    t = find_target(tid)
    val = eval_in(t["webSocketDebuggerUrl"], reader_js(site))
    print(json.dumps(val, ensure_ascii=False, indent=2))


def cmd_eval(tid, js):
    t = find_target(tid)
    print(json.dumps(eval_in(t["webSocketDebuggerUrl"], js), ensure_ascii=False, indent=2))


def click_js(selector, index=0):
    """selector 支持 CSS(..) 或 text:.. 按文本匹配(取第一个含该文本的 a/button/div)。"""
    if selector.startswith("text:"):
        txt = selector[5:]
        expr = f"""[...document.querySelectorAll('a,button,div,[role=tab],[class*=tab]')].find(e => (e.innerText||'').trim() === {json.dumps(txt)})"""
    else:
        expr = f"document.querySelectorAll({json.dumps(selector)})[{index}]"
    return f"""(() => {{
      const el = {expr};
      if (!el) return {{ok:false, reason:'no element', selector:{json.dumps(selector)}}};
      const r = el.getBoundingClientRect();
      el.click();
      return {{ok:true, rect:{{x:r.x,y:r.y,w:r.width,h:r.height}}, tag:el.tagName}};
    }})()"""


def cmd_click(tid, selector, index=0):
    t = find_target(tid)
    print(json.dumps(eval_in(t["webSocketDebuggerUrl"], click_js(selector, index)), ensure_ascii=False, indent=2))


def cmd_scroll(tid, direction):
    if direction == "down":
        js = "window.scrollBy(0, window.innerHeight * 0.8); 'scrolled down'"
    elif direction == "up":
        js = "window.scrollBy(0, -window.innerHeight * 0.8); 'scrolled up'"
    else:
        m = re.match(r"(-?\d+)px?", direction)
        px = int(m.group(1)) if m else 400
        js = f"window.scrollBy(0, {px}); 'scrolled {px}px'"
    t = find_target(tid)
    print(json.dumps(eval_in(t["webSocketDebuggerUrl"], js), ensure_ascii=False))


def cmd_wait(tid, selector, timeout_ms=8000):
    js = f"""(() => {{
      const sel = {json.dumps(selector)};
      const deadline = Date.now() + {timeout_ms};
      return new Promise((resolve) => {{
        const tick = () => {{
          const el = document.querySelector(sel);
          if (el) return resolve({{ok:true, found:true, selector:sel, waited: {timeout_ms} - (deadline-Date.now())}});
          if (Date.now() > deadline) return resolve({{ok:false, found:false, selector:sel}});
          setTimeout(tick, 200);
        }};
        tick();
      }});
    }})()"""
    t = find_target(tid)
    print(json.dumps(eval_in(t["webSocketDebuggerUrl"], js), ensure_ascii=False, indent=2))


def paginate_js(page):
    """雪球讨论区翻页: .pagination 内 input 设值 + Enter"""
    return f"""(() => {{
      const pag = document.querySelector('.pagination');
      if (!pag) return {{ok:false, reason:'no .pagination'}};
      const input = pag.querySelector('input');
      if (!input) return {{ok:false, reason:'no input in pagination'}};
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, '{page}');
      input.dispatchEvent(new Event('input', {{bubbles:true}}));
      input.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}}));
      input.dispatchEvent(new KeyboardEvent('keyup', {{key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}}));
      return {{ok:true, page:'{page}'}};
    }})()"""


def cmd_paginate(tid, page):
    """雪球讨论区翻页: .pagination 内 input 设值 + Enter"""
    t = find_target(tid)
    print(json.dumps(eval_in(t["webSocketDebuggerUrl"], paginate_js(page)), ensure_ascii=False, indent=2))


def solve_captcha(ws):
    """雪球滑块: 拖到 #aliyunCaptcha-sliding-body 右端贴边(记忆实测:拖到底才过)。
    返回 {triggered, solved, title, end}；solved=True 表示滑块元素已消失（通过）。"""
    probe = """(() => {
      const slider = document.querySelector('#aliyunCaptcha-sliding-slider');
      const body = document.querySelector('#aliyunCaptcha-sliding-body');
      if (!slider || !body) return {ok:false, reason:'no captcha elements'};
      const sr = slider.getBoundingClientRect();
      const br = body.getBoundingClientRect();
      return {ok:true, slider:{x:sr.x,y:sr.y,w:sr.width,h:sr.height},
              body:{x:br.x,y:br.y,w:br.width,h:br.height}};
    })()"""
    r = eval_in(ws, probe)
    if not r.get("ok"):
        return {"triggered": False, "solved": True, "reason": r.get("reason")}
    s, b = r["slider"], r["body"]
    start_x = s["x"] + s["w"] / 2
    y = s["y"] + s["h"] / 2
    end_x = b["x"] + b["w"] - s["w"] - 2  # 贴右端
    p = Page(ws)
    def send(method, params):
        return p.send(method, params)
    send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start_x, "y": y})
    send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": start_x, "y": y, "button": "left", "clickCount": 1})
    dist = end_x - start_x
    steps = max(8, int(dist / 40))
    for i in range(1, steps + 1):
        frac = i / steps
        # 加速-减速轨迹:先快后慢
        eased = frac * frac * (3 - 2 * frac) if frac < 0.7 else frac
        x = start_x + dist * eased
        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})
        time.sleep(0.03)
    # 末尾微调慢速到贴右
    send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": end_x, "y": y, "button": "left", "buttons": 1})
    time.sleep(0.1)
    send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": end_x, "y": y, "button": "left", "clickCount": 1})
    p.close()
    time.sleep(1.0)
    check = eval_in(ws, "({hasCaptcha: !!document.querySelector('#aliyunCaptcha-sliding-body'), title: document.title, url: location.href})")
    return {"triggered": True, "solved": not check.get("hasCaptcha"), "title": check.get("title"),
            "url": check.get("url"), "end": end_x}


def cmd_captcha(tid):
    """雪球滑块命令入口。"""
    t = find_target(tid)
    print(json.dumps(solve_captcha(t["webSocketDebuggerUrl"]), ensure_ascii=False, indent=2))


def cmd_title(tid):
    t = find_target(tid)
    print(json.dumps(eval_in(t["webSocketDebuggerUrl"],
                             "({url: location.href, title: document.title, ready: document.readyState})"),
                     ensure_ascii=False, indent=2))


def main():
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "new":
        cmd_new(sys.argv[2])
    elif cmd == "close":
        cmd_close(sys.argv[2])
    elif cmd == "clean":
        filepath = session_file_path() or ""
        if "--file" in sys.argv:
            filepath = sys.argv[sys.argv.index("--file") + 1]
        if not filepath:
            raise SystemExit("需要 DIVE_SESSION_FILE 环境变量或 --file <path>")
        cmd_clean(filepath)
    elif cmd == "dedupe":
        all_tabs = "--all" in sys.argv
        filepath = session_file_path() or ""
        if "--file" in sys.argv:
            filepath = sys.argv[sys.argv.index("--file") + 1]
        if not all_tabs and not filepath:
            raise SystemExit("需要 DIVE_SESSION_FILE 环境变量或 --file <path>（dedupe --all 不需要）")
        cmd_dedupe(filepath, all_tabs=all_tabs)
    elif cmd == "navigate":
        cmd_navigate(sys.argv[2], sys.argv[3])
    elif cmd == "eval":
        cmd_eval(sys.argv[2], sys.argv[3])
    elif cmd == "read":
        site = "generic"
        if "--site" in sys.argv:
            site = sys.argv[sys.argv.index("--site") + 1]
        cmd_read(sys.argv[2], site)
    elif cmd == "click":
        idx = 0
        if "--index" in sys.argv:
            idx = int(sys.argv[sys.argv.index("--index") + 1])
        cmd_click(sys.argv[2], sys.argv[3], idx)
    elif cmd == "scroll":
        cmd_scroll(sys.argv[2], sys.argv[3])
    elif cmd == "wait":
        tmo = 8000
        if "--timeout" in sys.argv:
            tmo = int(sys.argv[sys.argv.index("--timeout") + 1])
        cmd_wait(sys.argv[2], sys.argv[3], tmo)
    elif cmd == "paginate":
        cmd_paginate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "2")
    elif cmd == "captcha":
        cmd_captcha(sys.argv[2])
    elif cmd == "title":
        cmd_title(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
