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
        typer.echo("（无匹配事件，可新建）")
        return
    typer.echo(f"找到 {len(hits)} 个相关事件：")
    for h in hits:
        typer.echo(f"  [#{h['event_id']}] {h['event_title']} "
                   f"(重要度{h['event_importance']}, {h['event_status']})")
        typer.echo(f"      最近: {h['matched_title']}")
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
    industry: str = typer.Option(None, "--industry", help="行业名"),
    relevance: int = typer.Option(50, "--relevance"),
):
    """结构化写入一条 agent 整理后的消息。要么 --event <id> 归属，要么 --new-event 新建。"""
    if bool(event_id) == bool(new_event):
        typer.echo("错误：必须且只能指定 --event <id> 或 --new-event")
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
        iid = storage.upsert_industry(conn, industry)
        storage.link_event_industry(conn, eid, iid, relevance=relevance)
    conn.close()
    typer.echo(f"✓ 已保存消息 #{mid} → 事件 #{eid}（{'新建' if new_event else '归属已有'}）")


@app.command("update-event")
def update_event(event_id: int = typer.Argument(...),
                 latest_summary: str = typer.Option(..., "--latest-summary"),
                 importance: int = typer.Option(None, "--importance")):
    """刷新事件最新摘要。"""
    conn = _open()
    storage.update_event_summary(conn, event_id, latest_summary, importance=importance)
    conn.close()
    typer.echo(f"✓ 事件 #{event_id} 摘要已更新")


@app.command("resolve-event")
def resolve_event(event_id: int = typer.Argument(...)):
    """标记事件结束。"""
    conn = _open()
    storage.resolve_event(conn, event_id)
    conn.close()
    typer.echo(f"✓ 事件 #{event_id} 已标记 resolved")


@app.command()
def track(code: str = typer.Argument(...),
          name: str = typer.Option(..., "--name"),
          industry: str = typer.Option(None, "--industry"),
          watchlist: bool = typer.Option(False, "--watchlist"),
          priority: int = typer.Option(0, "--priority")):
    """添加/更新实体跟踪（watchlist 或探索发现的标的）。

    注意：upsert_stock 对 is_watchlist/priority 总是覆盖为传入值，因此
    重复 track 不带 --watchlist 会把已 watchlist 的标的重置为 0。如需保留
    watchlist 状态，跟踪脚本应在每次 track 时显式带上 --watchlist。
    """
    conn = _open()
    storage.upsert_stock(conn, code, name, industry=industry,
                         is_watchlist=1 if watchlist else 0, priority=priority)
    if industry:
        storage.upsert_industry(conn, industry)
    conn.close()
    typer.echo(f"✓ 已跟踪 {name} ({code})")


# ---------- 查询端 ----------

@app.command("query-stock")
def query_stock(code: str = typer.Argument(...), days: int = typer.Option(None, "--days")):
    """该股相关事件。"""
    conn = _open()
    evs = query.query_stock(conn, code, days=days)
    _print_events(conn, evs)
    conn.close()


@app.command("query-industry")
def query_industry(name: str = typer.Argument(...), days: int = typer.Option(None, "--days")):
    """该行业相关事件。"""
    conn = _open()
    evs = query.query_industry(conn, name, days=days)
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
        typer.echo("（无结果）")
        return
    for m in msgs:
        typer.echo(f"[{m['event_title']}] {m['title']} 重要度{m['importance']}")
    conn.close()


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
    storage.ack_refresh_request(conn, request_id)
    conn.close()
    typer.echo(f"✓ 刷新请求 #{request_id} 已完成")


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
            typer.echo(f"    · {m['title']}")
        if len(msgs) > 3:
            typer.echo(f"    · … 共{len(msgs)}条")
