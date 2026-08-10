"""ptrade2 风险控制命令组 — conditions / atr-sync / check-triggers / check-exright

T4 CLI 对齐：从 v1 `paper_trading/cli.py` 移植，import 全部替换为 paper_trading_v2，
命令驱动 SQLite（经 ConditionsManager/SqlStorage）。cli.py 保持薄分发，本模块通过
`@app.command` 在导入时注册到共享的 `app`（由 cli.py 末尾 `import conditions_cmd` 触发）。

cron 关键集：atr-sync + check-triggers + conditions 是交易循环的止损同步/破位检测链路。
"""
import typer
from typing import Optional

from paper_trading_v2.cli import app, _normalize_stock_name, _auto_exright_check
from paper_trading_v2.conditions import ConditionType, ConditionCategory, EventConditionType
from paper_trading_v2.conditions_manager import ConditionsManager
from paper_trading_v2.trading import PaperTrader
from paper_trading_v2.price_fetcher import StockPriceFetcher
from paper_trading_v2.kline_fetcher import KLineDataFetcher
from paper_trading_v2.exright_cache import ExRightCache


@app.command("check-exright")
def check_exright(
    stock_name: str = typer.Argument(..., help="股票名称"),
    force: bool = typer.Option(False, "--force", "-f", help="强制刷新缓存"),
):
    """检查并应用除权除息"""
    stock_name = _normalize_stock_name(stock_name)
    try:
        trader = PaperTrader()
        account = trader.get_account(stock_name)
        if not account:
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的账户", err=True)
            raise typer.Exit(1)

        if force:
            cache = ExRightCache()
            cache.clear(account.stock_code)

        changed, msg = _auto_exright_check(trader, stock_name)
        if changed:
            typer.echo(f"✅ {msg}")
        else:
            typer.echo(f"ℹ️ {msg}")
    except Exception as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("conditions")
def conditions_command(
    stock_name: str = typer.Argument(..., help="股票名称"),
    action: str = typer.Option("show", "--action", "-a", help="操作: show/set/update/remove/trigger/expire/check/event-set/event-remove/event-trigger/event-list"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式: pretty/markdown/json"),
    template: str = typer.Option("trigger-table", "--template", "-t", help="模板: trigger-table/audit-table/expired-table/execution-check/all"),
    # --set / --update params
    condition_type: Optional[str] = typer.Option(None, "--type", help="条件类型: trailing_stop/cost_protection/take_profit_1/take_profit_2/add_position"),
    price: Optional[float] = typer.Option(None, "--price", "-p", help="价格"),
    action_str: Optional[str] = typer.Option(None, "--action-str", help="触发动作描述"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="类别: hard/soft"),
    expiry_days: Optional[int] = typer.Option(None, "--expiry-days", "-e", help="软条件有效期（天）"),
    # --update reason
    reason: Optional[str] = typer.Option(None, "--reason", "-r", help="修改理由（Level 2）"),
    # --override params
    override_trigger: Optional[str] = typer.Option(None, "--override-trigger", help="强制复审触发器（逗号分隔）"),
    override_reason: Optional[str] = typer.Option(None, "--override-reason", help="解锁理由（Level 3，不少于20字）"),
    # --trigger / --expire
    trigger_price: Optional[float] = typer.Option(None, "--trigger-price", help="触发时价格"),
    # --update ATR（手动按 ATR 设定成本保护时传入，或省略由命令自动算）
    atr_value: Optional[float] = typer.Option(None, "--atr", help="ATR 值（用于按 ATR 设定 cost_protection；省略则自动取K线计算）"),
    # event condition params
    event_type: Optional[str] = typer.Option(None, "--event-type", help="事件类型: profit_protect(利润保护)/loss_protect(亏损保护)/tech_break(技术破位)/target_profit(目标价止盈, 别名 take_profit)/add_position(加仓)/fundamental(基本面)/market_risk(市场风险)"),
    event_id: Optional[str] = typer.Option(None, "--event-id", help="事件条件ID（用于移除/触发/过期）"),
):
    """条件管理：查看、设定、修改、触发、过期股票交易条件"""
    stock_name = _normalize_stock_name(stock_name)

    # 自动除权检查
    try:
        trader = PaperTrader()
        _auto_exright_check(trader, stock_name)
    except Exception:
        pass

    manager = ConditionsManager()

    # 获取当前价格（用于校验）
    def _get_current_price():
        try:
            from paper_trading_v2.portfolio import PortfolioManager
            pm = PortfolioManager()
            summary = pm.get_account_summary(stock_name)
            if summary and summary.get("positions", {}).get("current_price"):
                return summary["positions"]["current_price"]
        except Exception:
            pass
        return None

    def _get_avg_cost():
        try:
            from paper_trading_v2.portfolio import PortfolioManager
            pm = PortfolioManager()
            summary = pm.get_account_summary(stock_name)
            if summary and summary.get("positions", {}).get("total_quantity", 0) > 0:
                total_cost = summary["positions"]["total_cost"]
                total_qty = summary["positions"]["total_quantity"]
                return total_cost / total_qty if total_qty > 0 else 0
        except Exception:
            pass
        return None

    def _has_position():
        try:
            from paper_trading_v2.portfolio import PortfolioManager
            pm = PortfolioManager()
            summary = pm.get_account_summary(stock_name)
            if summary:
                return summary.get("positions", {}).get("total_quantity", 0) > 0
        except Exception:
            pass
        return False

    def _type_map(t: str) -> ConditionType:
        mapping = {
            "trailing_stop": ConditionType.TRAILING_STOP,
            "cost_protection": ConditionType.COST_PROTECTION,
            "take_profit_1": ConditionType.TAKE_PROFIT_1,
            "take_profit_2": ConditionType.TAKE_PROFIT_2,
            "add_position": ConditionType.ADD_POSITION,
        }
        return mapping.get(t)

    def _cat_map(c: str) -> ConditionCategory:
        mapping = {"hard": ConditionCategory.HARD, "soft": ConditionCategory.SOFT}
        return mapping.get(c)

    # ===== show =====
    if action == "show":
        if format == "markdown":
            output = manager.format_markdown(stock_name, template=template)
            typer.echo(output)
        elif format == "json":
            import json
            data = manager.format_json(stock_name)
            typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            output = manager.format_pretty(stock_name)
            typer.echo(output)
        return

    # ===== set =====
    if action == "set":
        if not condition_type or price is None or not category:
            typer.echo("❌ 错误: --set 需要 --type, --price, --category 参数", err=True)
            raise typer.Exit(1)

        ct = _type_map(condition_type)
        cc = _cat_map(category)
        if not ct:
            typer.echo(f"❌ 错误: 未知条件类型 '{condition_type}'", err=True)
            raise typer.Exit(1)
        if not cc:
            typer.echo(f"❌ 错误: 未知类别 '{category}'", err=True)
            raise typer.Exit(1)

        auto_link = (ct == ConditionType.COST_PROTECTION)

        record = manager.set_condition(
            stock_name=stock_name,
            condition_type=ct,
            price=price,
            action=action_str or "执行",
            category=cc,
            expiry_days=expiry_days,
            auto_link_cost=auto_link,
        )

        typer.echo(f"✅ 条件设定成功: {stock_name}")
        typer.echo(f"   类型: {ct.value}")
        typer.echo(f"   价格: ¥{price:.2f}")
        typer.echo(f"   类别: {cc.value}")
        if cc == ConditionCategory.SOFT and expiry_days:
            from paper_trading_v2.conditions import calculate_expiry_date
            typer.echo(f"   失效日期: {calculate_expiry_date(expiry_days)}")
        return

    # ===== update =====
    if action == "update":
        if not condition_type or price is None:
            typer.echo("❌ 错误: --update 需要 --type, --price 参数", err=True)
            raise typer.Exit(1)

        ct = _type_map(condition_type)
        if not ct:
            typer.echo(f"❌ 错误: 未知条件类型 '{condition_type}'", err=True)
            raise typer.Exit(1)

        current_price = _get_current_price() or price
        avg_cost = _get_avg_cost() or price
        has_pos = _has_position()

        # ATR：若手动更新 cost_protection 且未传 --atr，自动取K线计算
        atr_for_update = atr_value
        if atr_for_update is None and ct == ConditionType.COST_PROTECTION and has_pos:
            try:
                account = trader.storage.load_account(stock_name)
                if account and account.stock_code:
                    klines = KLineDataFetcher().fetch_kline_data(account.stock_code, "day", 30)
                    from paper_trading_v2.atr import compute_atr
                    atr_for_update = compute_atr(klines)
            except Exception:
                atr_for_update = None

        # Parse comma-separated trigger string
        active_triggers_list = []
        if override_trigger:
            active_triggers_list = [t.strip() for t in override_trigger.split(",") if t.strip()]

        result, record = manager.update_condition(
            stock_name=stock_name,
            condition_type=ct,
            new_price=price,
            current_price=current_price,
            avg_cost=avg_cost,
            has_position=has_pos,
            active_triggers=active_triggers_list,
            override_reason=override_reason or "",
            user_reason=reason or "",
            atr=atr_for_update,
        )

        if result.allowed:
            icon = "✅" if result.level.value == "auto" else "⚠️"
            typer.echo(f"{icon} 修改成功（{result.level.value.upper()}）")
            typer.echo(f"   条件: {ct.value}")
            typer.echo(f"   旧价格: ¥{record.get(ct).history[-1].old_price:.2f}")
            typer.echo(f"   新价格: ¥{price:.2f}")
            typer.echo(f"   理由: {result.message}")
            if result.requires_warning:
                typer.echo(f"   ⚠️ 警告: 这是重大变更，请确保理由充分")
        else:
            typer.echo(f"❌ 修改被阻断（{result.level.value.upper()}）")
            typer.echo(f"   {result.message}")
            raise typer.Exit(1)
        return

    # ===== remove =====
    if action == "remove":
        if not condition_type:
            typer.echo("❌ 错误: --remove 需要 --type 参数", err=True)
            raise typer.Exit(1)

        ct = _type_map(condition_type)
        if not ct:
            typer.echo(f"❌ 错误: 未知条件类型 '{condition_type}'", err=True)
            raise typer.Exit(1)

        if manager.remove_condition(stock_name, ct):
            typer.echo(f"✅ 已移除条件: {ct.value}")
        else:
            typer.echo(f"❌ 未找到条件: {ct.value}")
            raise typer.Exit(1)
        return

    # ===== trigger =====
    if action == "trigger":
        if not condition_type:
            typer.echo("❌ 错误: --trigger 需要 --type 参数", err=True)
            raise typer.Exit(1)

        ct = _type_map(condition_type)
        if not ct:
            typer.echo(f"❌ 错误: 未知条件类型 '{condition_type}'", err=True)
            raise typer.Exit(1)

        tp = trigger_price or price or _get_current_price() or 0

        record = manager.trigger_condition(stock_name, ct, tp)
        if record:
            typer.echo(f"✅ 条件已触发: {ct.value}")
            typer.echo(f"   触发价格: ¥{tp:.2f}")
        else:
            typer.echo(f"❌ 未找到条件: {ct.value}")
            raise typer.Exit(1)
        return

    # ===== expire =====
    if action == "expire":
        if not condition_type:
            typer.echo("❌ 错误: --expire 需要 --type 参数", err=True)
            raise typer.Exit(1)

        ct = _type_map(condition_type)
        if not ct:
            typer.echo(f"❌ 错误: 未知条件类型 '{condition_type}'", err=True)
            raise typer.Exit(1)

        record = manager.expire_condition(stock_name, ct)
        if record:
            typer.echo(f"✅ 条件已标记过期: {ct.value}")
        else:
            typer.echo(f"❌ 未找到条件: {ct.value}")
            raise typer.Exit(1)
        return

    # ===== check =====
    if action == "check":
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        expired = manager.check_expired(stock_name, current_date)
        if expired:
            typer.echo(f"⚠️ 发现 {len(expired)} 个过期条件:")
            for c in expired:
                typer.echo(f"   • {c.name}（过期日期: {c.expiry_date}）")
        else:
            typer.echo("✅ 无过期条件")
        return

    # ===== event-set =====
    if action == "event-set":
        if not event_type or price is None or not category:
            typer.echo("❌ 错误: --action event-set 需要 --event-type, --price, --category 参数", err=True)
            raise typer.Exit(1)

        # 命名别名归一化：吸收 ConditionType 与 EventConditionType 之间的命名不一致
        # 旧系统用 take_profit_1/2，新事件系统用 target_profit，两者中文都对应"止盈"
        event_type = {"take_profit": "target_profit"}.get(event_type, event_type)

        if event_type not in [e.value for e in EventConditionType]:
            valid = ", ".join([e.value for e in EventConditionType])
            typer.echo(f"❌ 错误: 未知事件类型 '{event_type}'，有效类型: {valid}", err=True)
            raise typer.Exit(1)

        cc = _cat_map(category)
        if not cc:
            typer.echo(f"❌ 错误: 未知类别 '{category}'", err=True)
            raise typer.Exit(1)

        event_id, record = manager.add_event_condition(
            stock_name=stock_name,
            event_type=event_type,
            price=price,
            action=action_str or "执行",
            category=cc,
            expiry_days=expiry_days,
        )

        if event_id:
            typer.echo(f"✅ 事件条件设定成功: {stock_name}")
            typer.echo(f"   ID: {event_id}")
            typer.echo(f"   事件类型: {event_type}")
            typer.echo(f"   名称: {record.get_event(event_id).name}")
            typer.echo(f"   价格: ¥{price:.2f}")
            typer.echo(f"   动作: {action_str or '执行'}")
            typer.echo(f"   类别: {cc.value}")
            if cc == ConditionCategory.SOFT and expiry_days:
                from paper_trading_v2.conditions import calculate_expiry_date
                typer.echo(f"   失效日期: {calculate_expiry_date(expiry_days)}")
        else:
            typer.echo("❌ 事件条件设定失败（请先初始化条件记录）", err=True)
            raise typer.Exit(1)
        return

    # ===== event-remove =====
    if action == "event-remove":
        if not event_id:
            typer.echo("❌ 错误: --action event-remove 需要 --event-id 参数", err=True)
            raise typer.Exit(1)

        if manager.remove_event_condition(stock_name, event_id):
            typer.echo(f"✅ 已移除事件条件: {event_id}")
        else:
            typer.echo(f"❌ 未找到事件条件: {event_id}")
            raise typer.Exit(1)
        return

    # ===== event-trigger =====
    if action == "event-trigger":
        if not event_id:
            typer.echo("❌ 错误: --action event-trigger 需要 --event-id 参数", err=True)
            raise typer.Exit(1)

        tp = trigger_price or price or _get_current_price() or 0

        record = manager.trigger_event_condition(stock_name, event_id, tp)
        if record:
            typer.echo(f"✅ 事件条件已触发: {event_id}")
            typer.echo(f"   触发价格: ¥{tp:.2f}")
        else:
            typer.echo(f"❌ 未找到事件条件: {event_id}")
            raise typer.Exit(1)
        return

    # ===== event-list =====
    if action == "event-list":
        record = manager.load_conditions(stock_name)
        if not record or not record.events:
            typer.echo("📭 无事件条件")
            return

        active_events = record.list_active_events()
        if not active_events:
            typer.echo("📭 无有效事件条件（可能全部已触发/过期）")
            return

        typer.echo(f"📋 {stock_name} 事件条件列表 ({len(active_events)}个):")
        for e in active_events:
            status_icon = "✅" if e.status.value == "active" else "🚫"
            typer.echo(f"   {status_icon} [{e.id}] {e.name} ¥{e.price:.2f} — {e.action} ({e.category.value})")
        return

    typer.echo(f"❌ 不支持的操作: {action}", err=True)
    raise typer.Exit(1)


@app.command("atr-sync")
def atr_sync_command(
    stock_name: Optional[str] = typer.Argument(None, help="股票名称；省略则遍历所有持仓账户"),
    k: Optional[float] = typer.Option(None, "--k", help="ATR 倍数（默认：trailing用2.5，cost用2.0）"),
    period: int = typer.Option(14, "--period", help="ATR 周期（默认14）"),
    kline_count: int = typer.Option(120, "--count", "-n", help="取K线根数（默认120）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只计算不写入"),
    init_peak: str = typer.Option("current", "--init-peak", help="首次peak初始化: current(默认,保守)/historical(激进)"),
    reset_peak: bool = typer.Option(False, "--reset-peak", help="重置peak为当前价（重新建仓后用）"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 pretty/json"),
):
    """ATR 动态止损同步：算 ATR + 更新 peak + 同步 trailing_stop 与 cost_protection。

    替代固定 3%/1.5% 缓冲。回测验证（两个独立样本）：ATR 止损样本外夏普 +0.53~0.55。
    cron 每日调用，或手动 `ptrade atr-sync 中科曙光`。
    """
    from paper_trading_v2.atr import compute_atr, ATR_K_TRAIL, ATR_K_COST
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.conditions import ConditionType

    trader = PaperTrader()
    cond_mgr = ConditionsManager(trader.storage)

    # 确定目标股票列表
    if stock_name:
        targets = [_normalize_stock_name(stock_name)]
    else:
        targets = trader.storage.list_accounts()

    results = []
    for name in targets:
        try:
            account = trader.storage.load_account(name)
            if not account:
                results.append({"stock": name, "status": "skip", "reason": "账户不存在"})
                continue
            if not account.stock_code:
                results.append({"stock": name, "status": "skip", "reason": "股票代码为空"})
                continue

            total_qty, total_cost = trader.get_remaining_position(account)
            if total_qty <= 0:
                results.append({"stock": name, "status": "skip", "reason": "空仓"})
                continue

            avg_cost = total_cost / total_qty

            # 取K线 + 实时价
            klines = KLineDataFetcher().fetch_kline_data(account.stock_code, "day", kline_count)
            atr = compute_atr(klines, period)
            if atr is None:
                results.append({"stock": name, "status": "skip",
                                "reason": f"K线不足{period+1}根"})
                continue

            rt = None
            rt_high = None
            current_price = None
            try:
                rt = StockPriceFetcher().get_realtime_price(account.stock_code)
                if rt:
                    rt_high = getattr(rt, "high", None)
                    current_price = getattr(rt, "current_price", None)
            except Exception:
                pass

            k_trail = k if k is not None else ATR_K_TRAIL
            k_cost = k if k is not None else ATR_K_COST

            # 计算预期值（用于输出和 dry_run）
            record = cond_mgr.load_conditions(name)
            ts_cond = record.get(ConditionType.TRAILING_STOP) if record else None
            cp_cond = record.get(ConditionType.COST_PROTECTION) if record else None
            old_trail = ts_cond.price if ts_cond else None
            old_cp = cp_cond.price if cp_cond else None
            old_peak = ts_cond.peak_price if ts_cond else None

            # peak 预期（与 sync_trailing_stop 实际写入保持一致：本轮过滤 + 旧peak污染重置）
            from paper_trading_v2.atr import merge_peak
            stale_peak_reset = False
            if reset_peak or (init_peak == "current" and old_peak is None):
                seed = current_price or (klines[-1].get("close") if klines else None)
                new_peak = seed
            else:
                round_klines, stale_peak = cond_mgr._filter_klines_by_round(name, klines or [], old_peak)
                if stale_peak:
                    # 旧 peak 来自上一轮 → 重新用当前价 seed，止损不套只升不降
                    seed = current_price or (klines[-1].get("close") if klines else None)
                    new_peak = seed
                    stale_peak_reset = True
                else:
                    new_peak = merge_peak(old_peak, round_klines, rt_high)

            expected_trail = round(new_peak - k_trail * atr, 2) if new_peak else None
            if stale_peak_reset:
                expected_trail_final = expected_trail  # 纠错性下移，不套 max
            else:
                expected_trail_final = max(old_trail, expected_trail) if (old_trail and expected_trail) else expected_trail
            expected_cp = round(avg_cost - k_cost * atr, 2)
            cost_floor_80 = round(avg_cost * 0.80, 2)
            if expected_cp < cost_floor_80:
                expected_cp = cost_floor_80

            entry = {
                "stock": name, "code": account.stock_code, "status": "ok",
                "atr": round(atr, 4), "avg_cost": round(avg_cost, 2),
                "peak_old": old_peak, "peak_new": new_peak,
                "trailing_stop_old": old_trail, "trailing_stop_new": expected_trail_final,
                "cost_protection_old": old_cp, "cost_protection_new": expected_cp,
            }
            results.append(entry)

            if not dry_run:
                cond_mgr.sync_trailing_stop(name, avg_cost, klines, atr, rt_high,
                                            k=k_trail, init_peak=init_peak,
                                            reset_peak=reset_peak, current_price=current_price)
                cond_mgr.sync_cost_protection(name, avg_cost, klines, atr, k_cost=k_cost)
        except Exception as e:
            results.append({"stock": name, "status": "error", "reason": str(e)})

    # 输出
    if format == "json":
        import json
        typer.echo(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(f"📊 ATR 同步（period={period}, k_trail={k if k is not None else ATR_K_TRAIL}, k_cost={k if k is not None else ATR_K_COST}{'，dry-run' if dry_run else ''}）")
        ok = 0
        skip = 0
        for r in results:
            name = r["stock"]
            if r["status"] == "ok":
                ok += 1
                typer.echo(f"  • {name} ({r.get('code','')}): ATR=¥{r['atr']:.2f} peak ¥{r['peak_old']}→¥{r['peak_new']}")
                if r.get("trailing_stop_old") is not None:
                    arrow = "→"
                    flag = "✅" if r["trailing_stop_new"] >= r["trailing_stop_old"] else "⚠️降"
                    typer.echo(f"      移动止损: ¥{r['trailing_stop_old']:.2f} {arrow} ¥{r['trailing_stop_new']:.2f} (peak−{k if k is not None else ATR_K_TRAIL}×ATR，只升不降) {flag}")
                if r.get("cost_protection_old") is not None:
                    typer.echo(f"      成本保护: ¥{r['cost_protection_old']:.2f} → ¥{r['cost_protection_new']:.2f} (成本¥{r['avg_cost']:.2f}−{k if k is not None else ATR_K_COST}×ATR)")
            elif r["status"] == "skip":
                skip += 1
                typer.echo(f"  • {name}: 跳过（{r['reason']}）⚠️")
            else:
                typer.echo(f"  • {name}: 报错（{r['reason']}）❌")
        typer.echo(f"汇总: {ok} 只同步, {skip} 只跳过" + ("（dry-run 未写入）" if dry_run else ""))


@app.command("check-triggers")
def check_triggers_command(
    stock_name: Optional[str] = typer.Argument(None, help="股票名称；省略则遍历所有持仓账户"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 pretty/json"),
):
    """止损触发检测：对比实时价与所有硬条件触发价，报告已破位的条件。

    只读检测，不修改条件 status、不执行卖出——供 cron 在 atr-sync 后调用，把已破位
    清单写进报告供人工/LLM 决策。修复"trigger-table 只反映手动标记、不反映实时破位"
    的缺陷（7/30 中科曙光移动止损¥91.49 被现价¥83.84 跌破却仍报"未触发"即此因）。

    退出码：有任意 breach → 1（便于脚本检测），无 breach → 0。
    """
    from paper_trading_v2.conditions_manager import ConditionsManager

    trader = PaperTrader()
    cond_mgr = ConditionsManager(trader.storage)

    if stock_name:
        targets = [_normalize_stock_name(stock_name)]
    else:
        targets = trader.storage.list_accounts()

    results = []
    total_breaches = 0
    for name in targets:
        try:
            account = trader.storage.load_account(name)
            if not account or not account.stock_code:
                results.append({"stock": name, "status": "skip", "reason": "账户不存在或代码为空"})
                continue

            total_qty, _ = trader.get_remaining_position(account)
            if total_qty <= 0:
                results.append({"stock": name, "status": "skip", "reason": "空仓"})
                continue

            # 取实时价（与 atr-sync 一致的防御式取法）
            current_price = None
            try:
                rt = StockPriceFetcher().get_realtime_price(account.stock_code)
                if rt:
                    current_price = getattr(rt, "current_price", None)
            except Exception:
                pass

            if current_price is None:
                results.append({"stock": name, "status": "skip", "reason": "实时价获取失败"})
                continue

            breaches = cond_mgr.check_triggers(name, current_price)
            total_breaches += len(breaches)
            results.append({
                "stock": name, "code": account.stock_code,
                "current_price": round(current_price, 2),
                "breaches": breaches,
            })
        except Exception as e:
            results.append({"stock": name, "status": "error", "reason": str(e)})

    # 输出
    if format == "json":
        import json
        typer.echo(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))
    else:
        for r in results:
            name = r["stock"]
            if r.get("status") == "skip":
                typer.echo(f"  • {name}: 跳过（{r['reason']}）⚠️")
                continue
            if r.get("status") == "error":
                typer.echo(f"  • {name}: 报错（{r['reason']}）❌")
                continue
            cp = r["current_price"]
            breaches = r["breaches"]
            if not breaches:
                typer.echo(f"  ✅ {name} ({r.get('code','')}) 现价¥{cp:.2f} 无触发")
                continue
            typer.echo(f"  🚨 {name} ({r.get('code','')}) 现价¥{cp:.2f} — {len(breaches)} 个条件已破位:")
            for b in breaches:
                arrow = "跌破" if b["direction"] == "down" else "涨破"
                typer.echo(
                    f"     • {b['name']}: 触发价¥{b['trigger_price']:.2f} 被{arrow} "
                    f"(穿透¥{b['breach_amount']:.2f} / {b['breach_pct']:.2f}%) → {b['action']}"
                )
        if total_breaches > 0:
            typer.echo(f"\n⚠️ 共 {total_breaches} 个条件已触发，请人工/LLM 复核（不自动卖出）")

    if total_breaches > 0:
        raise typer.Exit(1)
