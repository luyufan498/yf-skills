#!/usr/bin/env python3
"""知乎保登录清指纹：删指纹/tracking cookies、保留 z_c0（解决问题页 40362 限流）。

根因：知乎 question 页 40362 是 cookie 绑定的设备指纹限流（非 IP/账号）。
触发 cookie：JOID/osd/SESSIONID/captcha_session_v2/__snaker__id/gdxidpyhxdE/HMACCOUNT；
z_c0（登录）不触发 → 清指纹留 z_c0 即解除限流且保持登录。

用法：
  python3 zh_cookie_clean.py [--backup PATH]
"""
import argparse
import json
import sys
import urllib.request
import websocket

BROWSER = "http://localhost:9222"

# 触发 40362 的指纹/验证/tracking cookies（z_c0 登录 cookie 绝不删）
FINGERPRINT = {"JOID", "osd", "SESSIONID", "captcha_session_v2", "__snaker__id",
               "gdxidpyhxdE", "HMACCOUNT"}


def http_get(path):
    with urllib.request.urlopen(BROWSER + path, timeout=5) as r:
        return json.loads(r.read().decode())


def get_page_ws():
    """Network.getAllCookies 只在 page target 可用（browser target 会 -32601）。"""
    pages = [t for t in http_get("/json") if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("Chrome 9222 没有可用的页面 tab")
    return pages[0]["webSocketDebuggerUrl"]


class Page:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, origin="http://localhost:9222")
        self.mid = 0

    def send(self, method, params=None):
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                return m

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="知乎保登录清指纹")
    ap.add_argument("--backup", default="", help="清理前把全部 zhihu cookies 备份到该文件")
    args = ap.parse_args()

    try:
        p = Page(get_page_ws())
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{e}",
                          "hint": "Chrome 9222 未启动？请先以 --remote-debugging-port=9222 启动 Chrome"},
                         ensure_ascii=False))
        sys.exit(1)

    cookies = p.send("Network.getAllCookies")["result"]["cookies"]
    zh = [c for c in cookies if "zhihu" in c.get("domain", "")]
    if args.backup:
        json.dump(zh, open(args.backup, "w"))

    targets = [c for c in zh if c.get("name") in FINGERPRINT]
    deleted = 0
    for c in targets:
        r = p.send("Network.deleteCookies", {"name": c["name"], "domain": c["domain"],
                                             "path": c.get("path", "/")})
        if "error" not in r:
            deleted += 1

    cookies_after = p.send("Network.getAllCookies")["result"]["cookies"]
    zh_after = [c for c in cookies_after if "zhihu" in c.get("domain", "")]
    zc0_after = [c for c in zh_after if c.get("name") == "z_c0"]
    p.close()

    print(json.dumps({
        "ok": True,
        "deleted": [c["name"] for c in targets],
        "zhihu_before": len(zh),
        "zhihu_after": len(zh_after),
        "z_c0_preserved": bool(zc0_after),
        "hint": "若已删过则无需重复；40362 复发时重跑即可",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
