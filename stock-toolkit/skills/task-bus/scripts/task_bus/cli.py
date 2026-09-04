"""taskbus CLI：股票任务总线操作。"""
import json
from typing import Optional

import typer

from . import db

app = typer.Typer(help="股票任务总线：事件驱动的 agent 任务队列")


@app.command()
def init():
    """初始化任务库（建表）。"""
    path = db.get_db_path()
    conn = db.connect()
    conn.close()
    typer.echo(f"✅ 任务库就绪: {path}")


@app.command("add")
def add_event(
    type_: str = typer.Argument(..., help="事件类型: CANDIDATE/REFRESH/DEEP_DIVE/WATCH_ALERT/CALENDAR"),
    entity: str = typer.Argument(..., help="实体: 股票代码/行业名/事件id"),
    source: str = typer.Option("user", "--source", help="生产者标识"),
    priority: int = typer.Option(3, "--priority", min=1, max=5, help="优先级 1 最高"),
    payload: str = typer.Option(None, "--payload", help="JSON 附加参数"),
):
    """添加任务事件。"""
    try:
        p = json.loads(payload) if payload else None
        tid = db.add(type_, entity, source, priority, p)
        typer.echo(f"✅ 已入队 #{tid} [{type_}] {entity} (priority={priority}, source={source})")
        st = db.stats()
        typer.echo(f"   当前 pending: {st['by_status'].get('pending', 0)} 个 | 最新 ID: #{st['latest_id']}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(2)


@app.command("list")
def list_events(
    status: Optional[str] = typer.Option(None, "--status", help="按状态过滤"),
    type_: Optional[str] = typer.Option(None, "--type", help="按类型过滤"),
    limit: int = typer.Option(50, "--limit", help="最大条数"),
):
    """列出任务事件。"""
    evs = db.list_events(status=status, type_=type_, limit=limit)
    if not evs:
        typer.echo("(无事件)")
        return
    for e in evs:
        tag = {"pending": "⏳", "processing": "⚙️", "done": "✅", "failed": "❌"}.get(e["status"], "?")
        payload = f" payload={e['payload']}" if e["payload"] else ""
        typer.echo(f"{tag} #{e['id']} [{e['type']}] {e['entity']}  p{e['priority']} "
                   f"{e['status']} src={e['source']} @{e['created_at']}{payload}")
        if e["note"]:
            typer.echo(f"      ↳ {e['note']}")


@app.command("claim")
def claim_event(
    task_id: int = typer.Argument(..., help="事件 ID"),
    consumer: Optional[str] = typer.Option(
        None, "--consumer",
        help="消费者标识（写入 payload.claimed_by 供审计）。MSG_CANDIDATE/MSG_ORDER/"
             "MSG_REJUDGE 三类必须传 'msg-watch'（专用心跳），COLLECT 必须传 "
             "'news-collect'（采集心跳），ANALYSIS_REFRESH 必须传 "
             "'analysis-watch'（分析刷新心跳），否则拒绝认领"),
):
    """原子认领事件（pending→processing）。已被认领返回失败。

    硬门：news 挂单三类型仅 consumer=msg-watch 可认领；COLLECT 仅
    consumer=news-collect 可认领；ANALYSIS_REFRESH 仅 consumer=analysis-watch
    可认领（批量分析刷新链路，唯一消费者保证）；存量类型（CANDIDATE/
    CALENDAR/SLEEVE_FILL…）不校验，晨审/旧心跳照常 claim。
    """
    try:
        row = db.claim(task_id, consumer=consumer)
    except PermissionError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(3)
    if row is None:
        typer.echo(f"❌ #{task_id} 认领失败（不存在或已被认领/已结束）", err=True)
        raise typer.Exit(1)
    by = f" consumer={consumer}" if consumer else ""
    typer.echo(f"✅ 已认领 #{row['id']} [{row['type']}] {row['entity']} → processing{by}")


@app.command("done")
def done_event(
    task_id: int = typer.Argument(..., help="事件 ID"),
    note: str = typer.Option(None, "--note", help="消费结果备注"),
):
    """完成事件（processing→done）。"""
    if db.finish(task_id, "done", note):
        typer.echo(f"✅ #{task_id} 已完成")
    else:
        typer.echo(f"❌ #{task_id} 完成失败（状态不是 processing？）", err=True)
        raise typer.Exit(1)


@app.command("fail")
def fail_event(
    task_id: int = typer.Argument(..., help="事件 ID"),
    note: str = typer.Option("", "--note", help="失败原因"),
):
    """标记事件失败（processing→failed）。"""
    if db.finish(task_id, "failed", note):
        typer.echo(f"⚠️ #{task_id} 已标记失败: {note}")
    else:
        typer.echo(f"❌ #{task_id} 标记失败（状态不是 processing？）", err=True)
        raise typer.Exit(1)


@app.command("requeue")
def requeue_event(task_id: int = typer.Argument(..., help="事件 ID")):
    """失败事件重新入队（failed→pending）。"""
    if db.requeue(task_id):
        typer.echo(f"✅ #{task_id} 已重新入队")
    else:
        typer.echo(f"❌ #{task_id} 重试失败（状态不是 failed？）", err=True)
        raise typer.Exit(1)


@app.command("recover")
def recover_stale(stale_hours: float = typer.Option(2.0, "--stale-hours", help="超过 N 小时视为卡死")):
    """恢复卡死的 processing 事件（agent 崩溃）→ pending。"""
    n = db.recover(stale_hours)
    typer.echo(f"🔄 恢复 {n} 个卡死事件")


@app.command("stats")
def show_stats():
    """查看统计：各状态计数 + 最新 ID。"""
    st = db.stats()
    typer.echo(f"📊 总事件: {st['total']} 个")
    for s in ("pending", "processing", "done", "failed"):
        typer.echo(f"   {s}: {st['by_status'].get(s, 0)}")
    latest = st["latest"]
    if latest:
        typer.echo(f"   最新 #{latest['id']} [{latest['type']}] {latest['entity']} ({latest['status']})")
    else:
        typer.echo("   (暂无事件)")


@app.command("kv")
def kv_cmd(
    key: str = typer.Argument(..., help="KV 键（如 watch_scan_state）"),
    value: str = typer.Argument(None, help="值（缺省则读取该键）"),
):
    """读写 KV 状态存储（watch_scan 异动状态、atr-sync 日期等持久化状态）。"""
    if value is None:
        v = db.kv_get(key)
        typer.echo(json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else (v or "(空)"))
    else:
        db.kv_set(key, value)
        typer.echo(f"✅ {key} 已写入")


@app.command("watchpoint")
def watchpoint_cmd(
    action: str = typer.Argument(..., help="add / list / remove"),
    entity: str = typer.Argument(None, help="股票名（add/remove 需要）"),
    price: float = typer.Option(None, "--price", help="价格事件点上限（add 需要）；现价 ≤ price 触发，配 --min 则区间 [min, price] 触发"),
    code: str = typer.Option(None, "--code", help="股票代码（可选，检测用，缺省从池/账户查）"),
    note: str = typer.Option("", "--note", help="备注，如'买点下沿-重新评估'（add 可选）"),
    mode: str = typer.Option("eval", "--mode", help="触发语义: eval=L3观察评估(默认) / buy=L2建仓执行"),
    amount: Optional[float] = typer.Option(None, "--amount", help="建仓预算（mode=buy 时声明，触发后 allocate 金额，缺省则消费时拒绝执行）"),
    min_price: Optional[float] = typer.Option(None, "--min", help="价格区间下限（可选）：现价在 [min, price] 区间内才触发（单值不加 --min 行为不变）"),
):
    """价格事件点管理（存 kv_store 的 watch_points）。

    taskbus watchpoint add 光智科技 --price 240 --note "买点下沿-重新评估"          # L3 观察
    taskbus watchpoint add 赛力斯 --price 24.5 --mode buy --amount 200000 --note "建仓10%"  # L2 建仓点
    taskbus watchpoint list
    taskbus watchpoint remove 光智科技

    心跳 watch_scan 检测这些价格点：现价 ≤ price → 写 WATCH_ALERT
    - mode=eval（L3 观察窗）→ 唤醒分析 agent 重新评估是否升级 L2，不交易
    - mode=buy（L2 建仓点）→ 唤醒 agent 核验 → master-pool-allocate（budget）→ buy
    """
    from datetime import datetime

    key = "watch_points"
    points = db.kv_get(key) or {}
    if not isinstance(points, dict):
        points = {}
    if action == "add":
        if not entity or price is None:
            typer.echo("❌ add 需要 <股票> --price <价>", err=True)
            raise typer.Exit(1)
        if mode not in ("eval", "buy"):
            typer.echo("❌ --mode 应为 eval / buy", err=True)
            raise typer.Exit(1)
        if min_price is not None and not (0 < min_price < price):
            typer.echo("❌ --min 应满足 0 < min < price（否则区间永不触发或无效）", err=True)
            raise typer.Exit(1)
        if mode == "buy" and amount is None:
            typer.echo("⚠️ mode=buy 未传 --amount：触发后消费端将因预算缺失拒绝执行（请补 --amount 声明预算）", err=True)
        pts = points.setdefault(entity, [])
        pts.append({
            "code": code, "price": round(price, 2), "note": note,
            "mode": mode, "amount": amount,
            "min": round(min_price, 2) if min_price is not None else None,
            "added_at": datetime.now().strftime("%m-%d %H:%M"),
        })
        db.kv_set(key, points)
        kind = "L2建仓" if mode == "buy" else "L3观察"
        act = "唤醒核验建仓(allocate+buy)" if mode == "buy" else "唤醒评估"
        budget_txt = f"，预算 ¥{amount:,.0f}" if amount else ""
        range_txt = f"（区间 ¥{min_price:.2f}~{price:.2f}）" if min_price is not None else ""
        typer.echo(f"✅ {entity} {kind}价格点 ¥{price} 已添加{range_txt}（当前 {len(pts)} 个，现价 ≤ 触发时{act}{budget_txt}）")
    elif action == "list":
        if not points:
            typer.echo("(无价格事件点)")
            return
        typer.echo(f"📌 价格点（{len(points)} 只）：")
        for name, pts in points.items():
            for p in pts:
                m = p.get("mode", "eval")
                tag = "🛒买" if m == "buy" else "👀观"
                budget_txt = f" 预算¥{p.get('amount'):,.0f}" if p.get("amount") else ""
                typer.echo(f"  {tag} {name}  ¥{p['price']:<8} {p.get('note','')} ({m}){budget_txt}  ({p.get('added_at','')})")
                if p.get("min") is not None:
                    typer.echo(f"      └ 区间触发: ¥{p['min']:.2f} ≤ 现价 ≤ ¥{p['price']:.2f}")
    elif action == "remove":
        if not entity:
            typer.echo("❌ remove 需要 <股票>", err=True)
            raise typer.Exit(1)
        if entity in points:
            del points[entity]
            db.kv_set(key, points)
            typer.echo(f"✅ {entity} 价格点已移除")
        else:
            typer.echo(f"ℹ️ {entity} 无价格点")
    else:
        typer.echo("❌ action 应为 add / list / remove", err=True)
        raise typer.Exit(1)


@app.command("ack")
def ack_events(
    task_ids: list[int] = typer.Argument(..., help="事件 ID 列表（可多个）"),
    note: str = typer.Option(None, "--note", help="批量完成备注"),
):
    """批量完成事件（串行消费后一次确认）。"""
    ok, fail = 0, []
    for tid in task_ids:
        if db.finish(tid, "done", note):
            ok += 1
        else:
            fail.append(tid)
    typer.echo(f"✅ 完成 {ok} 个" + (f"，失败 {fail}" if fail else ""))


if __name__ == "__main__":
    app()
