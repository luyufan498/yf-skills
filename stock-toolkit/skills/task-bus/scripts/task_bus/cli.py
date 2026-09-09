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
    type_: str = typer.Argument(..., help="事件类型: DEEP_DIVE/WATCH_ALERT/CALENDAR/MSG_*/COLLECT/ANALYSIS_REFRESH"),
    entity: str = typer.Argument(..., help="实体: 股票代码/行业名/事件id"),
    source: str = typer.Option("user", "--source", help="生产者标识"),
    priority: int = typer.Option(3, "--priority", min=1, max=5, help="优先级 1 最高"),
    payload: str = typer.Option(None, "--payload", help="JSON 附加参数"),
    creator: str = typer.Option("", "--creator", help="对象创建者（方案 v3 §1.1 词表：msg-watch/"
                                "analysis-watch/check-open/l3-scan/portfolio-review/atr-auto/user；"
                                "与 --source=事件发射方区分）"),
):
    """添加任务事件。"""
    try:
        p = json.loads(payload) if payload else None
        tid = db.add(type_, entity, source, priority, p, creator=creator)
        creator_txt = f", creator={creator}" if creator else ""
        typer.echo(f"✅ 已入队 #{tid} [{type_}] {entity} (priority={priority}, source={source}{creator_txt})")
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
        help="消费者标识（写入 payload.claimed_by 供审计）。消息链路四类型 "
             "MSG_CANDIDATE/MSG_ORDER/MSG_REJUDGE/MSG_EXPIRE 必须传 'msg-watch' "
             "（专用心跳），COLLECT 必须传 "
             "'news-collect'（采集心跳），ANALYSIS_REFRESH 必须传 "
             "'analysis-watch'（分析刷新心跳），否则拒绝认领"),
):
    """原子认领事件（pending→processing）。已被认领返回失败。

    硬门：消息链路四类型（MSG_CANDIDATE/MSG_ORDER/MSG_REJUDGE/MSG_EXPIRE）仅
    consumer=msg-watch 可认领；COLLECT 仅
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
    note: str = typer.Option("", "--note", help="失败原因（人类可读，兼容旧用法）"),
    code: str = typer.Option(None, "--code", help="失败原因码（WP6-B 码表：gate_reject/no_position/"
                             "already_fulfilled/insufficient_funds|shares/stale_quote/halted/"
                             "band_left/band_skipped/expired/price_drifted/range_break/error）"),
    ref: str = typer.Option(None, "--ref", help="对象指针：'kind:id' 串或 JSON "
                            "（如 'watchpoint:wp:测试股:1757461200' 或 '{\"kind\":\"condition\",\"id\":44}'）"),
    result: str = typer.Option("failed", "--result", help="处置结果: done=已自动归档 / "
                               "cancelled=放弃 / failed=失败（默认）。行状态恒归 failed，"
                               "语义由 payload.exec.result 携带"),
):
    """标记事件失败（processing→failed），原因码落 payload.exec={result,code,ref,at}。

    WP6-B（方案 v3）：执行方带回原因码+对象指针，消费方（tech-watch/msg-watch）
    按 payload.exec 路由，无需回查上下文。
    """
    try:
        ok = db.fail_event(task_id, note=note, code=code, ref=ref, result=result)
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(2)
    if ok:
        code_txt = f" code={code}" if code else ""
        result_txt = f"（exec.result={result}）" if result != "failed" else ""
        typer.echo(f"⚠️ #{task_id} 已标记失败: {note}{code_txt}{result_txt}")
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
    action: str = typer.Argument(..., help="add / list / remove / reconcile"),
    entity: str = typer.Argument(None, help="股票名（add/remove 需要）"),
    price: float = typer.Option(None, "--price", help="价格事件点（add 需要）。eval/buy: 现价 ≤ price 触发（配 --min 区间 [min, price]）；sell: 现价 ≥ price 触发卖出（配 --min 区间 [price, min] 带内卖）"),
    code: str = typer.Option(None, "--code", help="股票代码（可选，检测用，缺省从池/账户查）"),
    note: str = typer.Option("", "--note", help="备注，如'买点下沿-重新评估'（add 可选）"),
    mode: str = typer.Option("eval", "--mode", help="触发语义: eval=技术组L2待命复检点(默认) / buy=L2建仓执行 / sell=卖出点：现价≥价触发卖出"),
    amount: Optional[float] = typer.Option(None, "--amount", help="建仓预算（mode=buy 时声明，触发后 allocate 金额，缺省则消费时拒绝执行）"),
    min_price: Optional[float] = typer.Option(None, "--min", help="价格区间另一沿（可选）。eval/buy: 下沿，现价 ∈ [min, price]；sell: 上沿，现价 ∈ [price, min]（单值不加 --min 行为不变）"),
    creator: str = typer.Option("", "--creator", help="对象创建者（方案 v3 §1.1 词表：msg-watch/"
                                "analysis-watch/check-open/l3-scan/portfolio-review/atr-auto/user；"
                                "缺省=当前会话写入方，落表 created_by 列）"),
    list_all: bool = typer.Option(False, "--all", help="list 时含 removed/triggered（默认只列 active）"),
):
    """价格事件点管理（watch_points 表 + 兼容期双写 kv_store('watch_points')）。

    taskbus watchpoint add 光智科技 --price 240 --note "买点下沿-重新评估"          # 技术组 L2 待命复检点
    taskbus watchpoint add 赛力斯 --price 24.5 --mode buy --amount 200000 --note "建仓10%"  # L2 建仓点
    taskbus watchpoint add 换出股 --price 12.0 --mode sell --code sh600871 --note "轮换出池限价卖"  # 卖出点
    taskbus watchpoint list [--all]      # 默认只列 active，--all 含 removed
    taskbus watchpoint remove 光智科技   # 软删（表置 removed + kv 移除）
    taskbus watchpoint reconcile         # 表 ↔ kv 对账（B4）

    心跳 watch_scan 检测这些价格点（兼容期读 kv 旧形状不变）：
    - mode=eval（技术组 L2 待命复检点）：现价 ≤ price → 唤醒分析 agent 重新评估是否升 L1，不交易
    - mode=buy（L2 建仓点）：现价 ≤ price → 唤醒 agent 核验 → master-pool-allocate（budget）→ buy
    - mode=sell（卖出点，2026-09-04）：现价 ≥ price（方向相反，涨到/回到目标价才卖）
      → WATCH_ALERT(mode=sell, direction=sell) 唤醒 C1 执行卖仓/减仓（限价卖）。
      轮换出池（allocate --rotation-out）由 master_pool 自动挂 sell 点。
    """
    from datetime import datetime

    key = "watch_points"
    if action == "add":
        if not entity or price is None:
            typer.echo("❌ add 需要 <股票> --price <价>", err=True)
            raise typer.Exit(1)
        if mode not in ("eval", "buy", "sell"):
            typer.echo("❌ --mode 应为 eval / buy / sell", err=True)
            raise typer.Exit(1)
        if min_price is not None:
            if mode == "sell":
                # sell 区间：price=下沿触发价、min=上沿封顶价 → 须 0 < price < min
                if not (0 < price < min_price):
                    typer.echo("❌ sell 的 --min 应满足 0 < price < min（price=下沿触发价，min=上沿封顶价，否则区间永不触发或无效）", err=True)
                    raise typer.Exit(1)
            elif not (0 < min_price < price):
                typer.echo("❌ --min 应满足 0 < min < price（否则区间永不触发或无效）", err=True)
                raise typer.Exit(1)
        if mode == "buy" and amount is None:
            typer.echo("⚠️ mode=buy 未传 --amount：触发后消费端将因预算缺失拒绝执行（请补 --amount 声明预算）", err=True)
        # 表化写入（B1）：wp_id 主键 + created_by 列；kv 双写走同一原子读改写入口
        wp_id = db.wp_insert(entity, price, mode=mode, code=code, min_price=min_price,
                             amount=amount, note=note, created_by=creator,
                             added_at=datetime.now().strftime("%m-%d %H:%M"))

        def _dual_write(points: dict) -> dict:
            pts = points.setdefault(entity, [])
            pts.append({
                "code": code, "price": round(price, 2), "note": note,
                "mode": mode, "amount": amount,
                "min": round(min_price, 2) if min_price is not None else None,
                "added_at": datetime.now().strftime("%m-%d %H:%M"),
            })
            return points

        points = db.kv_update(key, _dual_write)
        pts = points.get(entity, [])
        kind = "L2建仓" if mode == "buy" else ("卖出" if mode == "sell" else "L2复检")
        act = ("唤醒核验建仓(allocate+buy)" if mode == "buy"
               else "唤醒 C1 卖仓/减仓" if mode == "sell" else "唤醒评估")
        budget_txt = f"，预算 ¥{amount:,.0f}" if amount else ""
        if min_price is not None:
            range_txt = (f"（带内 ¥{price:.2f}~{min_price:.2f}）" if mode == "sell"
                         else f"（区间 ¥{min_price:.2f}~{price:.2f}）")
        else:
            range_txt = ""
        cmp_txt = "现价 ≥ 触发时" if mode == "sell" else "现价 ≤ 触发时"
        by_txt = f"，by={creator}" if creator else ""
        typer.echo(f"✅ {entity} {kind}价格点 ¥{price} 已添加{range_txt} wp={wp_id}（当前 {len(pts)} 个，{cmp_txt}{act}{budget_txt}{by_txt}）")
    elif action == "list":
        rows = db.wp_list(active_only=not list_all)
        if not rows:
            typer.echo("(无价格事件点)")
            return
        kv = db.kv_get(key) or {}
        n_entity = len({r["entity"] for r in rows})
        typer.echo(f"📌 价格点（{n_entity} 只 / {len(rows)} 点{'，含 removed/triggered' if list_all else ''}）：")
        for r in rows:
            m = r["mode"]
            tag = "🛒买" if m == "buy" else ("💰卖" if m == "sell" else "👀观")
            budget_txt = f" 预算¥{r['amount']:,.0f}" if r["amount"] else ""
            st = r["status"]
            by = f" by={r['created_by']}" if r["created_by"] else ""
            typer.echo(f"  {tag} {r['entity']}  ¥{r['price']:<8} {r['note'] or ''} ({m}){budget_txt}  "
                       f"({r['added_at']}) wp={r['wp_id']} st={st}{by}")
            if r["min"] is not None:
                if m == "sell":
                    typer.echo(f"      └ 区间触发: ¥{r['price']:.2f} ≤ 现价 ≤ ¥{r['min']:.2f}（带内卖出）")
                else:
                    typer.echo(f"      └ 区间触发: ¥{r['min']:.2f} ≤ 现价 ≤ ¥{r['price']:.2f}")
    elif action == "remove":
        if not entity:
            typer.echo("❌ remove 需要 <股票>", err=True)
            raise typer.Exit(1)
        n = db.wp_remove(entity)  # 软删：表置 removed，物理行保留

        def _kv_drop(points: dict) -> dict:
            points.pop(entity, None)  # 兼容期：kv 同步删除（watch_scan 兼容）
            return points

        db.kv_update(key, _kv_drop)
        if n:
            typer.echo(f"✅ {entity} 价格点已移除（软删 {n} 点，表行保留 status=removed）")
        else:
            typer.echo(f"ℹ️ {entity} 无价格点")
    elif action == "reconcile":
        rec = db.wp_reconcile()
        if rec["match"]:
            typer.echo(f"✅ 对账一致：表 active {rec['table_active']} 点 = kv {rec['kv_points']} 点")
        else:
            typer.echo(f"❌ 对账不一致：表 active {rec['table_active']} 点 vs kv {rec['kv_points']} 点", err=True)
            if rec["only_table"]:
                typer.echo(f"   仅表: {rec['only_table']}", err=True)
            if rec["only_kv"]:
                typer.echo(f"   仅 kv: {rec['only_kv']}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo("❌ action 应为 add / list / remove / reconcile", err=True)
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
