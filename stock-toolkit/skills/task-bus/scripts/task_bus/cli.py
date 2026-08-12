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
    type_: str = typer.Argument(..., help="事件类型: CANDIDATE/REFRESH/DEEP_DIVE/WATCH_ALERT/REVIEW/CALENDAR"),
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
def claim_event(task_id: int = typer.Argument(..., help="事件 ID")):
    """原子认领事件（pending→processing）。已被认领返回失败。"""
    row = db.claim(task_id)
    if row is None:
        typer.echo(f"❌ #{task_id} 认领失败（不存在或已被认领/已结束）", err=True)
        raise typer.Exit(1)
    typer.echo(f"✅ 已认领 #{row['id']} [{row['type']}] {row['entity']} → processing")


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
