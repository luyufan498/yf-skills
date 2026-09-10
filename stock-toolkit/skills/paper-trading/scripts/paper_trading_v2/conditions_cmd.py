"""ptrade2 风险控制命令组 — conditions / atr-sync / check-triggers / check-exright

T4 CLI 对齐：从 v1 `paper_trading/cli.py` 移植，import 全部替换为 paper_trading_v2，
命令驱动 SQLite（经 ConditionsManager/SqlStorage）。cli.py 保持薄分发，本模块通过
显式 `register(app)` 注册（cli.py 末尾调用），避免隐式副作用导入。

cron 关键集：atr-sync + check-triggers + conditions 是交易循环的止损同步/破位检测链路。
"""
import typer
from typing import Optional
import os

from paper_trading_v2.helpers import normalize_stock_name, auto_exright_check, get_stock_name_suggestions
from paper_trading_v2.conditions import ConditionType, ConditionCategory, EventConditionType
from paper_trading_v2.conditions_manager import ConditionsManager
from paper_trading_v2.trading import PaperTrader
from paper_trading_v2.price_fetcher import StockPriceFetcher
from paper_trading_v2.kline_fetcher import KLineDataFetcher
from paper_trading_v2.exright_cache import ExRightCache
from paper_trading_v2.exright_handler import ExRightHandler
from paper_trading_v2.portfolio import PortfolioManager


# 显式数量映射（2026-09-10 用户裁定："可以手动补一轮旧数据，方便迁移"）——
# 只列**已确认语义**的写法；不在表内 → 仍 fail-closed 不生成（绝不猜）。
# 取值：'all'=全部持仓；小数=该比例。**全部映射都按"清仓"取值**，与 conditions 腿现状
# （`check_price_triggers` 对卖方向一律 `ptrade2 sell --all`）**逐字一致** → 迁移不改语义；
# 若某线真实意图是减半/分批，须把 action 写成 `减仓50%` 这类可解析写法（生成期才敢按比例取）。
PROTECT_QTY_EXPLICIT = (
    (r'^执行$', 'all'),                        # 历史脏文字："按线执行"（=清仓）
    (r'成本保护\(成本-2ATR', 'all'),            # ATR 成本保护（深套恢复期棘轮）
    (r'成本保护-T\+10反转确认后恢复正式仓语义', 'all'),
    (r'价值反转仓成本保护', 'all'),
    (r'价值反转宽保护', 'all'),
    (r'消息仓成本保护', 'all'),
    (r'成本保护', 'all'),                       # 其余成本保护写法（-5%/-12% 的 % 是价格不是数量）
    (r'移动止损', 'all'),
    (r'恢复期线', 'all'),
    (r'亏损清仓', 'all'),
    (r'亏损止损', 'all'),
    (r'亏损预警', 'all'),
    (r'减仓', 'all'),                           # 未写百分比的"减仓"（写成 `减仓50%` 才会按比例）
)


def _protect_qty(action: str, held_qty: int):
    """把条件行的**比例语义在生成期算成数字**（D3：机械层只认数字）。

    只认显式写法，其余一律 None（fail-closed → 不生成兜底单，绝不猜数量）：
    - ``减仓50%`` / ``卖出 1/3`` 类 → 百分比（向下取整，宁少不多）；
    - ``清仓`` / ``全部`` / ``100%`` → 全部持仓；
    - **显式映射表**（``PROTECT_QTY_EXPLICIT``，2026-09-10 用户裁定"可以手动补一轮旧数据
      方便迁移"）：历史脏文字/未写数量的保护线按表取值（多数=清仓，与 conditions 腿
      现状 `--all` 同口径）；
    - 其它（如 ``价值反转仓成本保护-12%`` 之类**未列入表**的写法）→ None。

    注意：``成本保护-5%`` 这类文字里的百分比是**价格偏移**不是数量，故百分比只在
    带"减仓/卖出/减/卖"动词时才当数量解析（否则会把 -12% 当成只卖 12%）。
    """
    import re
    a = str(action or '')
    if held_qty <= 0:
        return None
    m = re.search(r'(?:减仓|卖出|减|卖)\s*(\d+(?:\.\d+)?)\s*%', a)
    if m:
        pct = float(m.group(1))
        if not (0 < pct <= 100):
            return None
        return int(held_qty * pct / 100.0)
    if re.search(r'清仓|全部|全清|清空', a) or re.search(r'100\s*%', a):
        return int(held_qty)
    for pat, how in PROTECT_QTY_EXPLICIT:
        if re.search(pat, a):
            if how == 'all':
                return int(held_qty)
            try:
                frac = float(how)
            except (TypeError, ValueError):
                return None
            return int(held_qty * frac) if 0 < frac <= 1 else None
    return None


def _ensure_protect_order(stock_name, code, held_qty, cp_cond, ts_cond, entry,
                          dry_run=False):
    """Phase 2 兜底单生成（在生成期做三件事，机械层只认结果）。

    - **D5 消解"用高者"**：跌破型卖单——线越高越紧，两条线只落更紧的那张；
    - **D3 数量数字**：由 `_protect_qty` 把 action 的比例语义算成股数，算不出则不生成；
    - 开关：`exec_layer.json → protect_orders.mode`（缺省 off）→ off 直接返回 None。

    返回 ``place_protect`` 结果（dict）或 None；不生成时写 ``entry["protect_skipped"]``。
    D6 抬升（同键只改价）由 ``place_protect`` 内部按槽状态决定。
    """
    from paper_trading_v2.exec_layer import protect_mode
    from paper_trading_v2.sleeve_order import SleeveOrder
    mode = protect_mode(stock_name)
    if mode == 'off':
        return None
    cands = []
    for kind, c in (('cost', cp_cond), ('trail', ts_cond)):
        if c is not None and getattr(c, 'price', None) \
                and getattr(c, 'status', 'active') == 'active':
            cands.append((kind, float(c.price), getattr(c, 'action', '')))
    # 2026-09-10 补：库里同一票可能有多条 active 保护线（宽保护/取严者/恢复期线并存），
    # 而 ConditionsRecord 每类型只回一条 → 只用它会挑到**更松**的线（切换守卫实测抓到
    # 凯莱英 ¥149.29<¥154.73、天孚 ¥233.60<¥238.07）。故再扫一遍库，取全局更紧者。
    try:
        from paper_trading_v2.config import get_workspace_config
        from paper_trading_v2.db import get_connection
        db = get_connection(get_workspace_config()['db_path'])
        try:
            for r in db.execute(
                    "SELECT cn.type, cn.price, cn.action FROM conditions cn "
                    "JOIN position a ON cn.account_id=a.id "
                    "WHERE a.code=? AND cn.status='active' AND cn.price IS NOT NULL "
                    "AND cn.type IN ('cost_protection','trailing_stop')", (code,)):
                cands.append(('cost' if r[0] == 'cost_protection' else 'trail',
                              float(r[1]), r[2] or ''))
        finally:
            db.close()
    except Exception:
        pass          # 库不可读（如单测环境无 conditions 表）→ 只用传入的两条线
    if not cands:
        entry['protect_skipped'] = '无 active 保护线'
        return None
    kind, line, action = max(cands, key=lambda x: x[1])
    qty = _protect_qty(action, int(held_qty))
    if not qty:
        entry['protect_skipped'] = f'action 无法机械判定数量（fail-closed 不生成）：{action!r}'
        return None
    if dry_run:
        return {'event_key': f'protect:{code}', 'action': 'dry-run（未写入）', 'kind': kind,
                'line': line, 'qty': qty, 'band_min': 0.0, 'band_max': round(line, 4),
                'order_ttl': None, 'status': 'pending_order'}
    return SleeveOrder().place_protect(code, line, qty, kind,
                                       reason=f'atr-sync {stock_name} mode={mode}')


def _slot_row(key):
    """查系统挂单槽的 (pending?, band_min, qty, fill_status, batch_id)；库不可读 → None。"""
    try:
        from paper_trading_v2.config import get_workspace_config
        from paper_trading_v2.db import get_connection
        db = get_connection(get_workspace_config()['db_path'])
        try:
            r = db.execute("SELECT status, band_min, qty, fill_status, batch_id "
                           "FROM event_slots WHERE event_key=?", (key,)).fetchone()
        finally:
            db.close()
    except Exception:
        return None
    if not r:
        return None
    st = (r['status'] if hasattr(r, 'keys') else r[0]) or ''
    band = (r['band_min'] if hasattr(r, 'keys') else r[1])
    qty = (r['qty'] if hasattr(r, 'keys') else r[2])
    fs = (r['fill_status'] if hasattr(r, 'keys') else r[3])
    bid = (r['batch_id'] if hasattr(r, 'keys') else r[4])
    return {'pending': st == 'pending_order', 'status': st,
            'band_min': float(band) if band is not None else None,
            'qty': int(qty) if qty is not None else None,
            'fill_status': fs, 'batch_id': bid}


def _tp_legs(avg_cost):
    """止盈两档目标价：+30%（腿1）/ +50%（腿2）× FIFO 剩余均价。"""
    from paper_trading_v2.atr import TP1_TRIGGER, TP2_TRIGGER
    return [(1, round(avg_cost * (1 + TP1_TRIGGER), 2)),
            (2, round(avg_cost * (1 + TP2_TRIGGER), 2))]


def _tp_qty(held_qty):
    """止盈腿数量 = 剩余仓位 1/3（比例语义在生成期数字化，机械层只认股数）。

    <3 股 → None（fail-closed：1/3 取整为 0，宁可不挂也不挂 0 股单）。
    """
    try:
        q = int(held_qty) // 3
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


def _ensure_tp_orders(stock_name, code, held_qty, avg_cost, entry, dry_run=False,
                      batch_id=None):
    """止盈挂单生成 / **覆盖式重挂**（Phase 3，2026-09-10）。

    覆盖式语义（方案 D2）：

    - 目标 = 当前**剩余仓位 FIFO 均价** × (1+30%) / (1+50%)，qty = 剩余 // 3（两腿各 1/3）；
    - 已有 pending 槽且 (价, 量) 一致 → **no-op**（'unchanged'：不写库、不产事件）；
    - 已有 pending 槽但价/量变了（加仓、成本基重算、除权） → 先
      `expire(reason='superseded')` 撤旧槽、再挂新槽——**撤+挂成对**，防同组两条腿同时可执行；
    - 已成交腿（``fill_status='filled'``） → **不复活**（A1 红线）；只有调用方显式给新批次
      （重建仓后）才 re-arm；
    - 开关 `exec_layer.json → tp_orders.mode`（缺省 off = 什么都不做）。
    """
    from paper_trading_v2.exec_layer import tp_mode
    from paper_trading_v2.sleeve_order import SleeveOrder, BAND_HI_SENTINEL
    mode = tp_mode(stock_name)
    if mode == 'off':
        return None
    try:
        avg_cost = float(avg_cost or 0)
    except (TypeError, ValueError):
        avg_cost = 0.0
    if avg_cost <= 0:
        entry['tp_skipped'] = 'FIFO 剩余均价不可用（fail-closed 不生成）'
        return None
    qty = _tp_qty(held_qty)
    if not qty:
        entry['tp_skipped'] = f'剩余仓位 {held_qty} 股 → 1/3 不足 1 股（fail-closed）'
        return None
    so = SleeveOrder()
    results = []
    for leg, price in _tp_legs(avg_cost):
        key = f'tp:{code}#{leg}'
        if dry_run:
            results.append({'event_key': key, 'action': 'dry-run（未写入）', 'leg': leg,
                            'line': price, 'qty': qty, 'band_min': price,
                            'band_max': BAND_HI_SENTINEL, 'order_ttl': None})
            continue
        cur = _slot_row(key)
        if cur and cur['pending'] and cur['qty'] == qty \
                and cur['band_min'] is not None and abs(cur['band_min'] - price) < 1e-6:
            results.append({'event_key': key, 'action': 'unchanged', 'leg': leg,
                            'line': price, 'qty': qty, 'band_min': price,
                            'band_max': BAND_HI_SENTINEL, 'order_ttl': None})
            continue
        if cur and cur['pending']:
            # 覆盖式重挂：撤旧槽（superseded）再挂新槽——两步同命令内连续完成
            so.expire(key, reason='superseded', source='atr-auto')
        results.append(so.place_take_profit(
            code, leg, price, qty,
            reason=f'tp-orders-sync {stock_name} mode={mode}', batch_id=batch_id))
    entry['tp_orders'] = results
    return results


def register(app):
    """注册风险控制命令组到共享 app（cli.py 末尾显式调用）。"""

    @app.command("check-exright")
    def check_exright(
        stock_name: str = typer.Argument(..., help="股票名称"),
        force: bool = typer.Option(False, "--force", "-f", help="强制清除除权缓存并重新检测"),
    ):
        """手动触发除权检测（自动除权检查的 CLI 封装）"""
        stock_name = normalize_stock_name(stock_name)
        trader = PaperTrader()
        account = trader.get_account(stock_name)
        if not account:
            suggestions = get_stock_name_suggestions(stock_name, PortfolioManager())
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的账户记录{suggestions}", err=True)
            raise typer.Exit(1)
        if force:
            if not account.stock_code:
                typer.echo("❌ 股票代码为空，无法清除除权缓存", err=True)
                raise typer.Exit(1)
            cache = ExRightCache()
            cache.clear(account.stock_code)
            typer.echo(f"✅ 已清除 {stock_name} 除权缓存")
        try:
            handler = ExRightHandler(trader, ExRightCache())
            changed, msg = handler.check_and_apply(stock_name, account)
            if changed:
                typer.echo(f"✅ {msg}")
            else:
                typer.echo(f"ℹ️ {msg}")
        except Exception as e:
            typer.echo(f"❌ 除权检查失败: {e}", err=True)
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
        # --name（2026-08-25 加入：宽保护标记等自定义条件名）
        cond_name: Optional[str] = typer.Option(None, "--name", help="自定义条件名（如 '宽保护-12%（价值反转）'——sync_cost_protection 识别该标记豁免 ATR 收紧）"),
        # --override params
        override_trigger: Optional[str] = typer.Option(None, "--override-trigger", help="强制复审触发器（逗号分隔）"),
        override_reason: Optional[str] = typer.Option(None, "--override-reason", help="解锁理由（Level 3，不少于20字）"),
        # --trigger / --expire
        trigger_price: Optional[float] = typer.Option(None, "--trigger-price", help="触发时价格"),
        # --update ATR（手动按 ATR 设定成本保护时传入，或省略由命令自动算）
        atr_value: Optional[float] = typer.Option(None, "--atr", help="ATR 值（用于按 ATR 设定 cost_protection；省略则自动取K线计算）"),
        # --event condition params
        event_type: Optional[str] = typer.Option(None, "--event-type", help="事件类型: profit_protect(利润保护)/loss_protect(亏损保护)/tech_break(技术破位)/target_profit(目标价止盈, 别名 take_profit)/add_position(加仓)/fundamental(基本面)/market_risk(市场风险)"),
        event_id: Optional[str] = typer.Option(None, "--event-id", help="事件条件ID（用于移除/触发/过期）"),
        # --created-by（v13/A4：对象创建者，失败路由依据 §1.1 词表；缺省 env PTRADE2_CREATOR，再缺省 user）
        created_by: Optional[str] = typer.Option(None, "--created-by", help="对象创建者（msg-watch/analysis-watch/check-open/l3-scan/portfolio-review/atr-auto/user…失败路由依据；缺省 env PTRADE2_CREATOR，再缺省 user）"),
        # --force（2026-08-27 加：跳过"保护价高于现价=设置即触发"校验）
        force: bool = typer.Option(False, "--force", help="跳过保护价>现价的设置即触发校验（手动补录/立即触发等特殊场景）"),
    ):
        """条件管理：查看、设定、修改、触发、过期股票交易条件"""
        stock_name = normalize_stock_name(stock_name)

        # 能力矩阵闸（sleeve-m1，方案 2.5）：消息组（grp=news）账户禁 conditions 全家写操作；
        # 读操作（show/event-list/check）放行。违例报错 + shadow_log(kind='gate_violation')。
        from paper_trading_v2.gate import conditions_write_actions, enforce
        if action in conditions_write_actions():
            try:
                enforce(stock_name, 'conditions_write')
            except ValueError as e:
                typer.echo(f"❌ {e}", err=True)
                raise typer.Exit(1)

        # 自动除权检查
        try:
            trader = PaperTrader()
            auto_exright_check(trader, stock_name)
        except Exception:
            pass

        manager = ConditionsManager()

        # v13/A4 创建者解析：--created-by 显式 > env PTRADE2_CREATOR > 'user'（词表 §1.1）
        def _resolve_creator():
            if created_by:
                return created_by
            return os.environ.get('PTRADE2_CREATOR') or 'user'

        # 获取当前价格（用于校验）
        def _get_current_price():
            try:
                pm = PortfolioManager()
                summary = pm.get_account_summary(stock_name)
                if summary and summary.get("positions", {}).get("current_price"):
                    return summary["positions"]["current_price"]
            except Exception:
                pass
            return None

        def _get_avg_cost():
            try:
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

            # 防"设置即触发"（2026-08-27 加，中芯 8/27 病态防护）：
            # 保护类条件（成本保护/移动止损）价格高于现价 = 设置即触发（价格已在保护价下方）。
            # 默认拒绝，--force 放行（手动补录/故意立即触发等特殊场景）。
            if not force and ct in (ConditionType.COST_PROTECTION, ConditionType.TRAILING_STOP):
                cur = _get_current_price()
                if cur and price > cur:
                    typer.echo(
                        f"❌ 拒绝: {ct.value} 设置价 ¥{price:.2f} > 当前价 ¥{cur:.2f}——"
                        f"设置即触发（价格已在保护价下方）。如需手动补录/立即触发，请加 --force。",
                        err=True,
                    )
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
                name=cond_name,
                created_by=_resolve_creator(),
            )

            typer.echo(f"✅ 条件设定成功: {stock_name}")
            typer.echo(f"   类型: {ct.value}")
            typer.echo(f"   价格: ¥{price:.2f}")
            typer.echo(f"   类别: {cc.value}")
            typer.echo(f"   创建者: {_resolve_creator()}")
            if cond_name:
                typer.echo(f"   名称: {cond_name}")
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

            current_price = _get_current_price()
            avg_cost = _get_avg_cost() or price
            has_pos = _has_position()

            # 防"设置即触发"（2026-08-27 加，与 set 分支同款——update 路径曾被旁路）：
            # 保护类条件价格高于现价 = 设置即触发。取不到现价时 fail-closed（拒绝并提示
            # --force），不用 price 自身当现价（那会让校验永不触发）。
            if not force and ct in (ConditionType.COST_PROTECTION, ConditionType.TRAILING_STOP):
                if current_price is None:
                    typer.echo(
                        f"❌ 拒绝: 无法获取 {stock_name} 当前价，无法验证保护价合理性。"
                        f"如确认要设置，请加 --force。",
                        err=True,
                    )
                    raise typer.Exit(1)
                if price > current_price:
                    typer.echo(
                        f"❌ 拒绝: {ct.value} 修改价 ¥{price:.2f} > 当前价 ¥{current_price:.2f}——"
                        f"设置即触发（价格已在保护价下方）。如需手动补录/立即触发，请加 --force。",
                        err=True,
                    )
                    raise typer.Exit(1)

            # ATR：若手动更新 cost_protection 且未传 --atr，自动取K线计算
            atr_for_update = atr_value
            if atr_for_update is None and ct == ConditionType.COST_PROTECTION and has_pos:
                try:
                    account = trader.storage.load_account(stock_name)
                    if account and account.stock_code:
                        # 2026-09-09 合入缓存：raw 缓存 + 除权折算（原直抓腾讯 qfq）
                        from paper_trading_v2.market_cache import fetch_kline_cached
                        klines = fetch_kline_cached(account.stock_code, count=30, adjust="qfq")
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
                created_by=_resolve_creator(),
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
        cron 每日调用，或手动 `ptrade2 atr-sync 中科曙光`。
        """
        from paper_trading_v2.atr import compute_atr, ATR_K_TRAIL, ATR_K_COST
        from paper_trading_v2.conditions import ConditionType

        trader = PaperTrader()
        cond_mgr = ConditionsManager(trader.storage)

        # 确定目标股票列表
        if stock_name:
            targets = [normalize_stock_name(stock_name)]
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
                # 2026-09-09 合入缓存（同 set 条件路径）：读时自愈 + 除权折算
                from paper_trading_v2.market_cache import fetch_kline_cached
                klines = fetch_kline_cached(account.stock_code, count=kline_count, adjust="qfq")
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
                # 显示层与 sync_cost_protection 实际逻辑一致：保本锁（2026-08-30）——
                # 本轮收盘浮盈≥15% 或已锁定（旧线≥成本）→ 保护线上移至成本
                from paper_trading_v2.atr import BREAKEVEN_TRIGGER
                _px = current_price or (klines[-1].get("close") if klines else None)
                if _px and avg_cost:
                    _locked = (old_cp or 0) >= avg_cost - 0.005
                    if _px >= avg_cost * (1 + BREAKEVEN_TRIGGER) or _locked:
                        # 锁定棘轮：≥成本且不降旧线（与实际写入层一致）
                        _be = max(round(avg_cost, 2), old_cp or 0)
                        if expected_cp is None or _be > expected_cp:
                            expected_cp = _be
                # 显示层与豁免逻辑一致：宽保护仓 ATR 收紧时显示保持旧价（防误导）
                # 保本锁穿透豁免（2026-08-30）：expected_cp≥成本（锁已生效）时不豁免
                if cp_cond and cp_cond.name and "宽保护" in cp_cond.name and old_cp is not None \
                        and not (expected_cp is not None and expected_cp >= avg_cost - 0.005):
                    if expected_cp > old_cp:
                        expected_cp = old_cp

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
                    # 止盈阶梯自动挂载（2026-08-30 止盈三件套②：+30%/+50% 各卖1/3）
                    _px = current_price or (klines[-1].get("close") if klines else None)
                    cond_mgr.sync_take_profit_ladder(name, avg_cost, _px)
                    # 显示层对齐实际写入（sync_cost_protection 深套三段式可能与
                    # 上方 expected_cp 正常逻辑不同——2026-08-27 中芯案例）
                    rec_after = cond_mgr.load_conditions(name)
                    cp_after = rec_after.get(ConditionType.COST_PROTECTION) if rec_after else None
                    if cp_after and cp_after.price is not None:
                        entry["cost_protection_old"] = old_cp
                        entry["cost_protection_new"] = cp_after.price
                    ts_after = rec_after.get(ConditionType.TRAILING_STOP) if rec_after else None
                    if ts_after and ts_after.price is not None:
                        entry["trailing_stop_old"] = old_trail
                        entry["trailing_stop_new"] = ts_after.price

                    # ---- Phase 2：系统兜底单（成本保护 / ATR 移动止损 → 挂单机制承载）----
                    # D5 生成期消解"用高者" + D3 数量数字 + D6 抬升只改价；
                    # 开关 exec_layer.json → protect_orders.mode（缺省 off，零影响）。
                    _po = _ensure_protect_order(name, account.stock_code, total_qty,
                                                cp_after, ts_after, entry)
                    if _po:
                        entry["protect_order"] = _po
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

    @app.command("protect-orders-sync")
    def protect_orders_sync(
        stock_name: Optional[str] = typer.Argument(None, help="股票名称；省略则遍历所有持仓账户"),
        dry_run: bool = typer.Option(False, "--dry-run", help="只算不写"),
        format: str = typer.Option("pretty", "--format", "-f", help="输出格式 pretty/json"),
    ):
        """**只生成/刷新系统兜底单，不重算保护线**（Phase 2，2026-09-10）。

        与 `atr-sync` 的分工：`atr-sync` 负责"算保护线"（每天首个交易 tick 一次，频率不动）；
        本命令只把**当前库里的保护线**落成/刷新挂单（`place_protect` 幂等：首挂/抬升/re-arm），
        用于 ①手动补一轮（迁移前把兜底单补齐）②逐票切换后立即生效 ③排障复核。

        开关：`exec_layer.json → protect_orders.mode`（缺省 off = 什么都不做）。
        数量语义与 `atr-sync` 完全一致（同一个 `_protect_qty` + `_ensure_protect_order`）。
        """
        from paper_trading_v2.conditions import ConditionType
        from paper_trading_v2.exec_layer import protect_mode

        trader = PaperTrader()
        cond_mgr = ConditionsManager(trader.storage)
        targets = [normalize_stock_name(stock_name)] if stock_name else trader.storage.list_accounts()
        results = []
        for name in targets:
            entry = {"stock": name}
            try:
                account = trader.storage.load_account(name)
                if not account or not account.stock_code:
                    entry.update({"status": "skip", "reason": "账户/代码缺失"})
                    results.append(entry)
                    continue
                total_qty, total_cost = trader.get_remaining_position(account)
                if total_qty <= 0:
                    entry.update({"status": "skip", "reason": "空仓"})
                    results.append(entry)
                    continue
                mode = protect_mode(name)
                entry["mode"] = mode
                if mode == 'off':
                    entry.update({"status": "skip", "reason": "开关 off"})
                    results.append(entry)
                    continue
                rec = cond_mgr.load_conditions(name)
                cp = rec.get(ConditionType.COST_PROTECTION) if rec else None
                ts = rec.get(ConditionType.TRAILING_STOP) if rec else None
                if not dry_run:
                    got = _ensure_protect_order(name, account.stock_code, total_qty,
                                                cp, ts, entry)
                else:
                    got = _ensure_protect_order(name, account.stock_code, total_qty,
                                                cp, ts, entry, dry_run=True)
                if got:
                    entry["protect_order"] = got
                entry["status"] = "ok"
            except Exception as e:                       # 单票失败不影响其余（同 atr-sync）
                entry.update({"status": "error", "reason": str(e)})
            results.append(entry)

        if format == "json":
            import json as _json
            typer.echo(_json.dumps({"results": results}, ensure_ascii=False,
                                   indent=2, default=str))
            return
        ok = [r for r in results if r.get("status") == "ok"]
        typer.echo(f"🛡️ 兜底单同步（{'dry-run，' if dry_run else ''}持仓 {len(ok)} 只）")
        for r in results:
            if r.get("status") != "ok":
                typer.echo(f"  • {r['stock']}: 跳过（{r.get('reason')}）")
                continue
            po = r.get("protect_order")
            if po:
                typer.echo(f"  • {r['stock']}: {po['action']} 线¥{po['line']} "
                           f"qty={po['qty']} band=[{po['band_min']},{po['band_max']}] "
                           f"ttl={po['order_ttl']}（mode={r.get('mode')}）")
            else:
                typer.echo(f"  • {r['stock']}: 未生成（{r.get('protect_skipped')}）")

    @app.command("tp-orders-sync")
    def tp_orders_sync(
        stock_name: Optional[str] = typer.Argument(None, help="股票名称；省略则遍历所有持仓账户"),
        dry_run: bool = typer.Option(False, "--dry-run", help="只算不写"),
        batch_id: Optional[int] = typer.Option(None, "--batch-id",
                                               help="显式新批次（重建仓后允许复活已成交腿）"),
        format: str = typer.Option("pretty", "--format", "-f", help="输出格式 pretty/json"),
    ):
        """止盈挂单生成 / **覆盖式重挂**（Phase 3，2026-09-10）。

        与 `conditions take_profit_*` 的分工：止盈阶梯的**执行**从 conditions 迁到挂单
        （`tp:<code>#1|#2`，涨破卖几何，qty=剩余/3，group_key=`<code>:tp`）。本命令按当前
        剩余仓位 FIFO 均价重算两档目标价并覆盖式重挂：价/量没变就 no-op，变了就
        「撤旧槽(superseded) + 挂新槽」，已成交腿不复活。

        开关：`exec_layer.json → tp_orders.mode`（缺省 off）。迁移期先跑 `--dry-run` 对账。
        """
        trader = PaperTrader()
        targets = [normalize_stock_name(stock_name)] if stock_name else trader.storage.list_accounts()
        results = []
        for name in targets:
            entry = {"stock": name}
            try:
                account = trader.storage.load_account(name)
                if not account or not account.stock_code:
                    entry.update({"status": "skip", "reason": "账户/代码缺失"})
                    results.append(entry)
                    continue
                total_qty, total_cost = trader.get_remaining_position(account)
                if total_qty <= 0:
                    entry.update({"status": "skip", "reason": "空仓"})
                    results.append(entry)
                    continue
                from paper_trading_v2.exec_layer import tp_mode
                mode = tp_mode(name)
                entry["mode"] = mode
                if mode == 'off':
                    entry.update({"status": "skip", "reason": "开关 off"})
                    results.append(entry)
                    continue
                avg = (total_cost / total_qty) if total_qty else 0
                entry["avg_cost"] = round(avg, 4)
                got = _ensure_tp_orders(name, account.stock_code, total_qty, avg, entry,
                                        dry_run=dry_run, batch_id=batch_id)
                entry["status"] = "ok"
                if not got:
                    pass
            except Exception as e:                        # 单票失败不影响其余
                entry.update({"status": "error", "reason": str(e)})
            results.append(entry)

        if format == "json":
            import json as _json
            typer.echo(_json.dumps({"results": results}, ensure_ascii=False,
                                   indent=2, default=str))
            return
        ok = [r for r in results if r.get("status") == "ok"]
        typer.echo(f"🎯 止盈挂单同步（{'dry-run，' if dry_run else ''}持仓 {len(ok)} 只）")
        for r in results:
            if r.get("status") != "ok":
                typer.echo(f"  • {r['stock']}: 跳过（{r.get('reason')}）")
                continue
            legs = r.get("tp_orders")
            if not legs:
                typer.echo(f"  • {r['stock']}: 未生成（{r.get('tp_skipped')}）")
                continue
            txt = "；".join(f"腿{l['leg']} {l['action']} ¥{l['line']} qty={l['qty']}"
                            for l in legs)
            typer.echo(f"  • {r['stock']}（均价¥{r.get('avg_cost')}，mode={r.get('mode')}）: {txt}")

    @app.command("exec-switch")
    def exec_switch(
        stock_name: str = typer.Argument(..., help="股票名称"),
        domain: str = typer.Option(..., "--domain", help="领域：protect|tp"),
        to: str = typer.Option(..., "--to", help="切到 orders（挂单执行+停 conditions 腿）| shadow（回退+恢复 conditions 腿）"),
        dry_run: bool = typer.Option(False, "--dry-run", help="只算不写"),
    ):
        """执行层逐票切换（2026-09-10：把"停 conditions 腿"从人工 UPDATE 变成显式开关）。

        **为什么必须原子**：只切白名单而不停 conditions 腿 = 同一破线被**卖两次**
        （双卖；清仓单可能形成负持仓）。本命令把「改挂单白名单」与「停/恢复对应
        conditions 腿」绑成一个动作，并把动过的行 id 写进 `exec_layer.json.switched`
        （审计 + 回滚依据）。

        - `--domain protect`：conditions 侧 = cost_protection/trailing_stop，开关段 protect_orders
        - `--domain tp`：conditions 侧 = take_profit_1/take_profit_2，开关段 tp_orders
        - `--to orders`：白名单加该票 + 停对应 conditions 腿
        - `--to shadow`：白名单移出该票 + 只恢复「本次切换前是 active」的行
        """
        import json as _json
        import os as _os
        from datetime import datetime
        from paper_trading_v2.config import get_workspace_config
        from paper_trading_v2.db import get_connection
        from paper_trading_v2.exec_layer import config_path

        domain = (domain or '').strip().lower()
        to = (to or '').strip().lower()
        if domain not in ('protect', 'tp'):
            typer.echo("❌ --domain 必须是 protect|tp", err=True)
            raise typer.Exit(1)
        if to not in ('orders', 'shadow'):
            typer.echo("❌ --to 必须是 orders|shadow", err=True)
            raise typer.Exit(1)
        name = normalize_stock_name(stock_name)
        key = 'protect_orders' if domain == 'protect' else 'tp_orders'
        types = ('cost_protection', 'trailing_stop') if domain == 'protect' \
            else ('take_profit_1', 'take_profit_2')

        cfg_path = config_path()
        try:
            with open(cfg_path, encoding='utf-8') as f:
                cfg = _json.load(f)
            cfg = cfg if isinstance(cfg, dict) else {}
        except (OSError, ValueError):
            cfg = {}
        sec = cfg.get(key)
        sec = dict(sec) if isinstance(sec, dict) else {}
        wl = [str(x) for x in (sec.get('exec_stocks') or [])]
        switched = dict(cfg.get('switched') or {})

        db = get_connection(get_workspace_config()['db_path'])
        try:
            rows = db.execute(
                "SELECT cn.id, cn.status FROM conditions cn JOIN position a "
                "ON cn.account_id=a.id WHERE a.stock=? AND cn.type IN (%s)"
                % ",".join("?" * len(types)), (name, *types)).fetchall()
            rows = [(r[0] if not hasattr(r, 'keys') else r['id'],
                     (r[1] if not hasattr(r, 'keys') else r['status'])) for r in rows]
            if to == 'orders':
                if name not in wl:
                    wl.append(name)
                sec['mode'] = 'orders'
                sec['exec_stocks'] = wl
                stop = [rid for rid, st in rows if st == 'active']
                rec = dict(switched.get(name) or {})
                ids = dict(rec.get(domain) or {})
                for rid in stop:
                    ids[str(rid)] = 'active'
                for rid, st in rows:
                    if st == 'suspended' and str(rid) not in ids:
                        ids[str(rid)] = 'suspended'    # 本次切换前就已停 → 回退时不擅自放开
                rec[domain] = ids
                rec.setdefault('ts', datetime.now().isoformat())
                switched[name] = rec
                if not dry_run:
                    if stop:
                        db.executemany("UPDATE conditions SET status='suspended' WHERE id=? "
                                       "AND status='active'", [(r,) for r in stop])
                    db.commit()
            else:
                if name in wl:
                    wl.remove(name)
                sec['exec_stocks'] = wl
                sec['mode'] = 'orders' if wl else 'shadow'
                rec = dict(switched.get(name) or {})
                ids = dict(rec.get(domain) or {})
                back = [int(rid) for rid, prev in ids.items() if prev == 'active']
                if not dry_run:
                    if back:
                        db.executemany("UPDATE conditions SET status='active' WHERE id=? "
                                       "AND status='suspended'", [(r,) for r in back])
                    db.commit()
                rec.pop(domain, None)
                if rec:
                    switched[name] = rec
                else:
                    switched.pop(name, None)
            if not dry_run:
                try:
                    with db:
                        db.execute("INSERT INTO shadow_log (kind,key,payload,created_at) "
                                   "VALUES ('exec_switch',?,?,?)",
                                   (name, _json.dumps({'domain': domain, 'to': to,
                                                       'rows': rows[:60]},
                                                      ensure_ascii=False, default=str),
                                    datetime.now().isoformat(timespec='seconds')))
                except Exception:
                    pass
                # 槽式状态汇总（回读核对用）
                after = db.execute(
                    "SELECT cn.status, COUNT(*) FROM conditions cn JOIN position a "
                    "ON cn.account_id=a.id WHERE a.stock=? AND cn.type IN (%s) "
                    "GROUP BY cn.status" % ",".join("?" * len(types)),
                    (name, *types)).fetchall()
                after = {((r[0] if not hasattr(r, 'keys') else r['status'])): 
                         (r[1] if not hasattr(r, 'keys') else r[1]) for r in after}
            else:
                after = {'（dry-run 未改）': 0}
        finally:
            db.close()

        if not dry_run:
            cfg[key] = sec
            if switched:
                cfg['switched'] = switched
            tmp = cfg_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
            _os.replace(tmp, cfg_path)

        typer.echo(f"{'（dry-run）' if dry_run else '✅'} exec-switch {name}: "
                   f"domain={domain} → {to}")
        typer.echo(f"   白名单（{key}.exec_stocks）= {sec.get('exec_stocks')}")
        typer.echo(f"   conditions 腿（{'/'.join(types)}）状态 = {after}")
        if to == 'orders':
            typer.echo("   ⚠️ 该票 conditions 腿已停（不删，可 --to shadow 回退）；"
                       "恢复前勿手动放开，否则双卖")

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
        trader = PaperTrader()
        cond_mgr = ConditionsManager(trader.storage)

        if stock_name:
            targets = [normalize_stock_name(stock_name)]
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

    return app
