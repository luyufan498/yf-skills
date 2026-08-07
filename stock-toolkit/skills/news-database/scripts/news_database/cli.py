"""newsdb 命令行入口：采集端（建/归/刷/收）、查询端（查/搜/取重要）、协作端（请求/确认）。"""

import typer

from news_database import storage, query
from news_database import search as search_mod  # noqa: N812 (命令 search 与模块重名，别名规避)
from news_database.config import get_db_path
from news_database.db import connect, init_db

app = typer.Typer(
    name="newsdb",
    help="📰 News Database - 独立新闻库",
    add_completion=False,
    no_args_is_help=True,
)


def _open():
    conn = connect(get_db_path())
    init_db(conn)  # 幂等，确保库存在
    return conn


# ---------- 采集端 ----------

@app.command()
def init():
    """初始化数据库（幂等）。"""
    conn = _open()
    conn.close()
    typer.echo(f"✓ 数据库就绪: {get_db_path()}")


@app.command()
def lookup(keywords: str = typer.Argument(...),
           entity_type: str = typer.Option(None, "--entity-type"),
           limit: int = typer.Option(10, "--limit")):
    """入库前查重/查事件归属。返回已有事件候选。"""
    conn = _open()
    hits = search_mod.lookup_events(conn, keywords, entity_type=entity_type, limit=limit)
    if not hits:
        conn.close()
        typer.echo("（无匹配事件，可新建）")
        return
    typer.echo(f"找到 {len(hits)} 个相关事件：")
    for h in hits:
        typer.echo(f"  [#{h['event_id']}] {h['event_title']} "
                   f"(重要度{h['event_importance']}, {h['event_status']})")
        typer.echo(f"      最近: {h['matched_title']}")
        if h['matched_summary']:
            typer.echo(f"      摘要: {h['matched_summary']}")
    conn.close()


@app.command()
def event(event_id: int = typer.Argument(...)):
    """查看单个事件详情（完整消息时间线）。"""
    conn = _open()
    ev, msgs = storage.get_event_with_messages(conn, event_id)
    if not ev:
        typer.echo(f"事件 #{event_id} 不存在")
        raise typer.Exit(code=1)
    typer.echo(f"#{ev['id']} {ev['title']}  [{ev['status']}] 重要度{ev['importance']}")
    if ev["latest_summary"]:
        typer.echo(f"  最新: {ev['latest_summary']}")
    for m in msgs:
        typer.echo(f"  · {m['title']} ({m['occurred_at'] or m['fetched_at']})")
    conn.close()


@app.command("save")
def save(
    title: str = typer.Option(..., "--title"),
    summary: str = typer.Option(None, "--summary"),
    entity_type: str = typer.Option("market", "--entity-type"),
    time_sensitivity: str = typer.Option("medium", "--sensitivity"),
    importance: int = typer.Option(3, "--importance"),
    keywords: str = typer.Option(None, "--keywords"),
    source: str = typer.Option(None, "--source"),
    url: str = typer.Option(None, "--url"),
    occurred_at: str = typer.Option(None, "--occurred-at"),
    event_id: int = typer.Option(None, "--event", help="归属已有事件"),
    new_event: bool = typer.Option(False, "--new-event", help="新建事件"),
    stock: str = typer.Option(None, "--stock", help="逗号分隔的股票代码"),
    industry: str = typer.Option(None, "--industry", help="逗号分隔的行业名"),
    relevance: int = typer.Option(50, "--relevance"),
):
    """结构化写入一条 agent 整理后的消息。要么 --event <id> 归属，要么 --new-event 新建。"""
    if bool(event_id) == bool(new_event):
        typer.echo("错误：必须且只能指定 --event <id> 或 --new-event")
        raise typer.Exit(code=2)
    VALID_ENTITY_TYPES = {"stock", "industry", "policy", "market"}
    if entity_type not in VALID_ENTITY_TYPES:
        typer.echo(f"错误：--entity-type 必须是 {'/'.join(sorted(VALID_ENTITY_TYPES))} 之一")
        raise typer.Exit(code=2)
    conn = _open()
    if new_event:
        eid = storage.create_event(conn, title, entity_type=entity_type,
                                   time_sensitivity=time_sensitivity, importance=importance)
    else:
        eid = event_id
        ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not ev:
            typer.echo(f"事件 #{eid} 不存在")
            raise typer.Exit(code=3)
    mid = storage.add_message(conn, eid, title, summary=summary, url=url, source=source,
                              occurred_at=occurred_at, importance=importance, keywords=keywords)
    if stock:
        for code in [s.strip() for s in stock.split(",") if s.strip()]:
            storage.link_event_stock(conn, eid, code, relevance=relevance)
    if industry:
        for ind in [x.strip() for x in industry.split(",") if x.strip()]:
            storage.link_event_industry(conn, eid, ind, relevance=relevance)
    conn.close()
    typer.echo(f"✓ 已保存消息 #{mid} → 事件 #{eid}（{'新建' if new_event else '归属已有'}）")


@app.command("update-event")
def update_event(event_id: int = typer.Argument(...),
                 latest_summary: str = typer.Option(..., "--latest-summary"),
                 importance: int = typer.Option(None, "--importance")):
    """刷新事件最新摘要。"""
    conn = _open()
    n = storage.update_event_summary(conn, event_id, latest_summary, importance=importance)
    conn.close()
    if n == 0:
        typer.echo(f"事件 #{event_id} 不存在")
        raise typer.Exit(code=1)
    typer.echo(f"✓ 事件 #{event_id} 摘要已更新")


@app.command("resolve-event")
def resolve_event(event_id: int = typer.Argument(...)):
    """标记事件结束。"""
    conn = _open()
    n = storage.resolve_event(conn, event_id)
    conn.close()
    if n == 0:
        typer.echo(f"事件 #{event_id} 不存在")
        raise typer.Exit(code=1)
    typer.echo(f"✓ 事件 #{event_id} 已标记 resolved")


@app.command()
def track(code: str = typer.Argument(...),
          name: str = typer.Option(..., "--name"),
          industry: str = typer.Option(None, "--industry"),
          market_cap: float = typer.Option(None, "--market-cap", help="总市值（亿元）"),
          watchlist: bool = typer.Option(False, "--watchlist"),
          priority: int = typer.Option(0, "--priority")):
    """添加/更新实体跟踪（watchlist 或探索发现的标的）。

    注意：upsert_stock 对 is_watchlist/priority 总是覆盖为传入值，因此
    重复 track 不带 --watchlist 会把已 watchlist 的标的重置为 0。如需保留
    watchlist 状态，跟踪脚本应在每次 track 时显式带上 --watchlist。
    """
    conn = _open()
    storage.upsert_stock(conn, code, name, industry=industry, market_cap=market_cap,
                         is_watchlist=1 if watchlist else 0, priority=priority)
    if industry:
        storage.upsert_industry(conn, industry)
    conn.close()
    typer.echo(f"✓ 已跟踪 {name} ({code})" + (f" 市值{market_cap}亿" if market_cap else ""))


# ---------- 查询端 ----------

@app.command("query-stock")
def query_stock(code: str = typer.Argument(...), days: int = typer.Option(None, "--days")):
    """该股相关事件。"""
    conn = _open()
    stock = storage.get_stock(conn, code)
    if stock and stock["market_cap"]:
        typer.echo(f"📊 {stock['name']} ({code}) 市值 {stock['market_cap']} 亿")
    evs = query.query_stock(conn, code, days=days)
    _print_events(conn, evs)
    conn.close()


@app.command("query-industry")
def query_industry(name: str = typer.Argument(...), days: int = typer.Option(None, "--days")):
    """该行业相关事件（支持别名 + 父带子；未命中时给出候选提示）。"""
    conn = _open()
    ids = query.resolve_industry_ids(conn, name)
    if ids is None:
        cands = query.suggest_industries(conn, name)
        if cands:
            typer.echo(f"未找到行业 '{name}'，你可能想查：")
            for c in cands:
                aliases = storage.list_industry_aliases(conn, c["id"])
                alias_str = f" (别名: {', '.join(aliases[:5])})" if aliases else ""
                rels = query.related_industries(conn, c["id"])
                rel_str = ""
                if rels:
                    rel_names = []
                    for r in rels:
                        rrow = conn.execute("SELECT name FROM industries WHERE id=?",
                                            (int(r["other_id"]),)).fetchone()
                        rel_names.append(f"{rrow['name']} (related, s{r['strength']})"
                                         if rrow else f"#{r['other_id']}")
                    rel_str = f"\n      相关: {', '.join(rel_names)}"
                typer.echo(f"  #{c['id']} {c['name']}{alias_str}{rel_str}")
        else:
            typer.echo(f"未找到行业 '{name}'，请检查拼写或用 'newsdb industry-aliases list' 查看现有行业")
        conn.close()
        return
    evs = query.query_industry_by_ids(conn, ids, days=days)
    _print_events(conn, evs)
    conn.close()


@app.command("query-market")
def query_market(days: int = typer.Option(None, "--days")):
    """市场/A股大盘/政策/宏观 事件。"""
    conn = _open()
    evs = query.query_market(conn, days=days)
    _print_events(conn, evs)
    conn.close()


@app.command()
def important(min_importance: int = typer.Option(4, "--min-importance"),
              days: int = typer.Option(None, "--days")):
    """高重要度事件。"""
    conn = _open()
    evs = query.query_important(conn, min_importance=min_importance, days=days)
    _print_events(conn, evs)
    conn.close()


@app.command()
def search(keywords: str = typer.Argument(...), limit: int = typer.Option(10, "--limit")):
    """FTS 全文检索消息。"""
    conn = _open()
    msgs = search_mod.search_messages(conn, keywords, limit=limit)
    if not msgs:
        conn.close()
        typer.echo("（无结果）")
        return
    for m in msgs:
        typer.echo(f"[{m['event_title']}] {m['title']} 重要度{m['importance']}")
    conn.close()


# ---------- 行业管理 ----------

@app.command("industry-aliases")
def industry_aliases(
    action: str = typer.Argument(...),
    name: str = typer.Argument(None),
    alias: str = typer.Option(None, "--alias"),
):
    """行业别名管理：add <行业名> --alias <别名> / list [<行业名>]"""
    conn = _open()
    if action == "add":
        if not name or not alias:
            typer.echo("用法: industry-aliases add <行业名> --alias <别名>")
            raise typer.Exit(code=2)
        iid = storage.upsert_industry(conn, name)
        storage.add_industry_alias(conn, iid, alias)
        typer.echo(f"✓ 已为行业 '{name}' (#{iid}) 登记别名 '{alias}'")
    elif action == "list":
        if name:
            row = conn.execute("SELECT id FROM industries WHERE name=?", (name,)).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT industry_id AS id FROM industry_aliases WHERE alias_name=?", (name,)).fetchone()
            if not row:
                typer.echo(f"未找到行业 '{name}'（用 'newsdb industry-aliases list' 查看全部）")
            else:
                iid = row["id"]
                aliases = storage.list_industry_aliases(conn, iid)
                typer.echo(f"行业 '{name}' (#{iid}) 别名: {', '.join(aliases) if aliases else '无'}")
        else:
            for r in conn.execute("SELECT * FROM industries ORDER BY name"):
                aliases = storage.list_industry_aliases(conn, r["id"])
                typer.echo(f"#{r['id']} {r['name']}" + (f" (别名: {', '.join(aliases)})" if aliases else ""))
    else:
        typer.echo(f"未知动作 '{action}'，支持 add / list")
        raise typer.Exit(code=2)
    conn.close()


@app.command("industry-hierarchy")
def industry_hierarchy(
    action: str = typer.Argument(...),
    name: str = typer.Argument(None),
    parent: str = typer.Option(None, "--parent"),
):
    """行业层级：set-parent <子行业> --parent <父行业>"""
    conn = _open()
    if action == "set-parent":
        if not name or not parent:
            typer.echo("用法: industry-hierarchy set-parent <子行业> --parent <父行业>")
            raise typer.Exit(code=2)
        child_id, parent_id = storage.set_industry_parent(conn, name, parent)
        typer.echo(f"✓ 已设 '{name}' (#{child_id}) 为 '{parent}' (#{parent_id}) 的子行业")
    else:
        typer.echo(f"未知动作 '{action}'，支持 set-parent")
        raise typer.Exit(code=2)
    conn.close()


@app.command("industry-relate")
def industry_relate(
    name_a: str = typer.Argument(...),
    to: str = typer.Option(..., "--to"),
    strength: int = typer.Option(60, "--strength"),
):
    """登记行业间关联（relations 表，rel_type='related'）。"""
    conn = _open()
    ia, ib = storage.relate_industries(conn, name_a, to, strength=strength)
    conn.close()
    typer.echo(f"✓ 已登记行业关联: '{name_a}' (#{ia}) ↔ '{to}' (#{ib}) (strength {strength})")


@app.command("industry-sync")
def industry_sync(dry_run: bool = typer.Option(False, "--dry-run")):
    """回填现有库：登记别名 + 建行业关联 + 设层级（幂等，可用 --dry-run 预览不写库）。"""
    from news_database import industry_sync as sync_mod
    conn = _open()
    summary = sync_mod.apply_sync_config(conn, dry_run=dry_run)
    conn.close()
    action = "回填完成" if not dry_run else "dry-run 预览（未写库）"
    typer.echo(f"✓ {action}: 别名 {summary['aliases_added']} 个, 关联 {summary['relations_added']} 对, "
               f"层级 {summary['parents_set']} 个, 行业关联 {summary['industry_links']} 个")


# ---------- 协作端 ----------

@app.command("request-refresh")
def request_refresh(stock_code: str = typer.Argument(...),
                    signal: str = typer.Option(..., "--signal"),
                    reason: str = typer.Option(None, "--reason"),
                    priority: int = typer.Option(3, "--priority")):
    """分析 agent 检测到异动→写刷新请求（带语义化 signal）。"""
    conn = _open()
    rid = storage.create_refresh_request(conn, stock_code, signal, reason=reason, priority=priority)
    conn.close()
    typer.echo(f"✓ 已提交刷新请求 #{rid} 用于 {stock_code}")


@app.command("refresh-requests")
def list_refresh_requests(status: str = typer.Option("pending", "--status")):
    """列出异动刷新请求。"""
    conn = _open()
    reqs = storage.list_refresh_requests(conn, status=status)
    if not reqs:
        conn.close()
        typer.echo(f"（{status} 请求为空）")
        return
    for r in reqs:
        typer.echo(f"[#{r['id']}] {r['stock_code']} ({r['reason'] or '-'}) p{r['priority']}")
        typer.echo(f"    {r['signal_text']}")
    conn.close()


@app.command("ack-refresh")
def ack_refresh(request_id: int = typer.Argument(...)):
    """新闻 agent 处理完请求后标记完成。"""
    conn = _open()
    n = storage.ack_refresh_request(conn, request_id)
    conn.close()
    if n == 0:
        typer.echo(f"刷新请求 #{request_id} 不存在")
        raise typer.Exit(code=1)
    typer.echo(f"✓ 刷新请求 #{request_id} 已完成")


@app.command("scan-status")
def scan_status(
    action: str = typer.Argument(...),
    scope_type: str = typer.Argument(...),
    scope_id: str = typer.Argument(...),
):
    """扫描状态：set <scope_type> <scope_id> 记录本次 / get <scope_type> <scope_id> 查看上次。"""
    from news_database import scan as scan_mod
    conn = _open()
    if action == "set":
        scan_mod.set_last_scan(conn, scope_type, scope_id)
        typer.echo(f"✓ 已记录扫描: {scope_type}/{scope_id}")
    elif action == "get":
        last = scan_mod.get_last_scan(conn, scope_type, scope_id)
        typer.echo(f"{scope_type}/{scope_id} 上次扫描: {last or '未扫描'}")
    else:
        typer.echo(f"未知动作 '{action}'，支持 set / get")
        raise typer.Exit(code=2)
    conn.close()


@app.command("scan-list")
def scan_list():
    """列出采集 agent 应扫描的 scope（从库拉取，新加股票/行业自动出现）。"""
    from news_database import scan as scan_mod
    conn = _open()
    scopes = scan_mod.list_scan_scopes(conn)
    if not scopes:
        typer.echo("（无扫描 scope，先用 newsdb track 添加股票/行业）")
        conn.close()
        return
    typer.echo("=== 扫描清单 ===")
    for s in scopes:
        flag = "🔔" if s["is_watchlist"] else "  "
        last = s["last_scan"] or "未扫描"
        typer.echo(f"  {flag} {s['scope_type']}/{s['scope_id']} 上次扫描: {last}")
    conn.close()


# ---------- helpers ----------

def _print_events(conn, evs):
    if not evs:
        typer.echo("（无事件）")
        return
    for e in evs:
        ev, msgs = storage.get_event_with_messages(conn, e["id"])
        typer.echo(f"#{ev['id']} {ev['title']}  [{ev['status']}] 重要度{ev['importance']} "
                   f"消息{ev['msg_count']}条 ({ev['updated_at']})")
        for m in msgs[:3]:
            url = m['url'] or ''
            typer.echo(f"    · [msg#{m['id']}] {m['title']} {url}")
        if len(msgs) > 3:
            typer.echo(f"    · … 共{len(msgs)}条")
