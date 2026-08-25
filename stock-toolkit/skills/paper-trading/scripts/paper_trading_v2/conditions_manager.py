"""条件管理器

提供条件的增删改查、规则校验、与交易操作联动等功能。
"""

import json
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from paper_trading_v2.conditions import (
    Condition, ConditionType, ConditionCategory, ConditionStatus,
    ConditionLevel, ConditionsRecord, ConditionChange,
    ConditionRules, ValidationResult,
    format_trigger_table, format_audit_table, calculate_expiry_date,
    OVERRIDE_TRIGGERS,
)
from paper_trading_v2.storage import JsonStorage
from paper_trading_v2.models import Account


class ConditionsManager:
    """条件管理器"""

    def __init__(self, storage: JsonStorage = None):
        self.storage = storage or JsonStorage()

    def _db_conn(self):
        from paper_trading_v2.db import get_connection
        return get_connection(self.storage.db_path)

    def load_conditions(self, stock_name: str) -> Optional[ConditionsRecord]:
        """从 SQLite 水合 ConditionsRecord（conditions dict + events 列表，保序）"""
        conn = self._db_conn()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE stock_name=?", (stock_name,)).fetchone()
            if not row:
                return None
            account_id = row[0]
            conditions = {}
            events = []
            cond_rows = conn.execute(
                "SELECT * FROM conditions WHERE account_id=? ORDER BY is_event, seq",
                (account_id,)).fetchall()
            for r in cond_rows:
                cond = Condition(
                    id=r['cond_uid'] or r['type'],
                    type=r['type'], name=r['name'] or '', price=r['price'] or 0.0,
                    action=r['action'] or '', category=r['category'],
                    expiry_date=r['expiry_date'], status=r['status'],
                    auto_link_cost=bool(r['auto_link_cost']), peak_price=r['peak_price'],
                    created_at=r['created_at'] or datetime.now().isoformat(),
                    modified_at=r['modified_at'] or datetime.now().isoformat(),
                )
                hist_rows = conn.execute(
                    "SELECT * FROM condition_history WHERE condition_id=? ORDER BY id",
                    (r['id'],)).fetchall()
                cond.history = [ConditionChange(
                    old_price=h['old_price'], new_price=h['new_price'],
                    reason=h['reason'] or '', timestamp=h['timestamp'] or '',
                    level=h['level'], override_triggers=json.loads(h['override_triggers'] or '[]'),
                ) for h in hist_rows]
                if r['is_event']:
                    events.append(cond)
                else:
                    conditions[r['cond_key'] or r['type']] = cond
            return ConditionsRecord(
                stock_name=stock_name,
                updated_at=datetime.now().isoformat(),
                conditions=conditions, events=events,
            )
        finally:
            conn.close()

    def save_conditions(self, record: ConditionsRecord) -> Path:
        """把 ConditionsRecord 事务化 upsert 进 SQLite"""
        conn = self._db_conn()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE stock_name=?", (record.stock_name,)).fetchone()
            if not row:
                raise ValueError(f"账户 '{record.stock_name}' 不存在，请先初始化")
            account_id = row[0]
            record.updated_at = datetime.now().isoformat()
            with conn:
                conn.execute("DELETE FROM condition_history WHERE condition_id IN "
                             "(SELECT id FROM conditions WHERE account_id=?)", (account_id,))
                conn.execute("DELETE FROM conditions WHERE account_id=?", (account_id,))
                for i, (key, cond) in enumerate(record.conditions.items()):
                    self._insert_condition(conn, account_id, key, 0, i, cond)
                for i, cond in enumerate(record.events):
                    self._insert_condition(conn, account_id, None, 1, i, cond)
            return Path(self.storage.db_path)
        finally:
            conn.close()

    def _insert_condition(self, conn, account_id, key, is_event, seq, cond):
        cur = conn.execute(
            "INSERT INTO conditions (account_id, cond_key, is_event, cond_uid, type, name, price, "
            "action, category, expiry_date, status, auto_link_cost, peak_price, created_at, "
            "modified_at, seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (account_id, key, is_event, cond.id, cond.type, cond.name, cond.price, cond.action,
             cond.category, cond.expiry_date, cond.status, int(cond.auto_link_cost),
             cond.peak_price, cond.created_at, cond.modified_at, seq))
        cid = cur.lastrowid
        for h in cond.history:
            conn.execute(
                "INSERT INTO condition_history (condition_id, old_price, new_price, reason, "
                "timestamp, level, override_triggers) VALUES (?,?,?,?,?,?,?)",
                (cid, h.old_price, h.new_price, h.reason, h.timestamp, h.level,
                 json.dumps(h.override_triggers)))

    def init_conditions(self, stock_name: str,
                        trailing_stop: float = None,
                        cost_protection: float = None,
                        take_profit_1: float = None,
                        take_profit_2: float = None,
                        add_position: float = None,
                        avg_cost: float = None) -> ConditionsRecord:
        """初始化条件（建仓时调用）"""
        record = ConditionsRecord(stock_name=stock_name)
        now = datetime.now().isoformat()

        # 移动止损
        if trailing_stop is not None:
            record.set(Condition(
                type=ConditionType.TRAILING_STOP,
                name="移动止损",
                price=round(trailing_stop, 2),
                action="减仓50%",
                category=ConditionCategory.HARD,
                created_at=now,
                modified_at=now,
                history=[ConditionChange(
                    old_price=0,
                    new_price=round(trailing_stop, 2),
                    reason="首次设定",
                    level=ConditionLevel.LEVEL_1,
                )],
            ))

        # 成本保护
        if cost_protection is not None:
            buffered_price = round(cost_protection * (1 - self.COST_PROTECTION_BUFFER), 2)
            record.set(Condition(
                type=ConditionType.COST_PROTECTION,
                name="成本保护",
                price=buffered_price,
                action="亏1.5%清仓",
                category=ConditionCategory.HARD,
                auto_link_cost=True,
                created_at=now,
                modified_at=now,
                history=[ConditionChange(
                    old_price=0,
                    new_price=buffered_price,
                    reason=f"首次设定（绑定持仓成本，缓冲{self.COST_PROTECTION_BUFFER*100:.1f}%）",
                    level=ConditionLevel.LEVEL_1,
                )],
            ))

        # 止盈1
        if take_profit_1 is not None:
            record.set(Condition(
                type=ConditionType.TAKE_PROFIT_1,
                name="止盈条件1",
                price=round(take_profit_1, 2),
                action="减仓30%",
                category=ConditionCategory.SOFT,
                expiry_date=calculate_expiry_date(7),
                created_at=now,
                modified_at=now,
                history=[ConditionChange(
                    old_price=0,
                    new_price=round(take_profit_1, 2),
                    reason="首次设定",
                    level=ConditionLevel.LEVEL_1,
                )],
            ))

        # 止盈2
        if take_profit_2 is not None:
            record.set(Condition(
                type=ConditionType.TAKE_PROFIT_2,
                name="止盈条件2",
                price=round(take_profit_2, 2),
                action="减仓50%",
                category=ConditionCategory.SOFT,
                expiry_date=calculate_expiry_date(7),
                created_at=now,
                modified_at=now,
                history=[ConditionChange(
                    old_price=0,
                    new_price=round(take_profit_2, 2),
                    reason="首次设定",
                    level=ConditionLevel.LEVEL_1,
                )],
            ))

        # 加仓条件
        if add_position is not None:
            record.set(Condition(
                type=ConditionType.ADD_POSITION,
                name="加仓条件",
                price=round(add_position, 2),
                action="加仓至目标仓位",
                category=ConditionCategory.SOFT,
                expiry_date=calculate_expiry_date(7),
                created_at=now,
                modified_at=now,
                history=[ConditionChange(
                    old_price=0,
                    new_price=round(add_position, 2),
                    reason="首次设定",
                    level=ConditionLevel.LEVEL_1,
                )],
            ))

        self.save_conditions(record)
        return record

    def update_condition(self, stock_name: str,
                         condition_type: ConditionType,
                         new_price: float,
                         current_price: float = None,
                         avg_cost: float = None,
                         has_position: bool = True,
                         active_triggers: List[str] = None,
                         override_reason: str = "",
                         user_reason: str = "",
                         atr: float = None,
                         k_cost: float = 2.0) -> tuple:
        """
        更新条件（带规则校验）

        返回: (ValidationResult, ConditionsRecord)
        """
        record = self.load_conditions(stock_name)
        if not record:
            return (
                ValidationResult(
                    allowed=False,
                    level=ConditionLevel.BLOCKED,
                    message=f"❌ 未找到股票 '{stock_name}' 的条件记录。请先初始化。",
                    requires_log=True,
                    requires_warning=True,
                ),
                None,
            )

        condition = record.get(condition_type)
        if not condition:
            return (
                ValidationResult(
                    allowed=False,
                    level=ConditionLevel.BLOCKED,
                    message=f"❌ 条件类型 '{condition_type.value}' 不存在。请先使用 --set 初始化。",
                    requires_log=True,
                    requires_warning=True,
                ),
                None,
            )

        old_price = condition.price

        # 根据条件类型选择校验规则
        if condition_type == ConditionType.TRAILING_STOP:
            result = ConditionRules.validate_trailing_stop_change(
                old_price=old_price,
                new_price=new_price,
                avg_cost=avg_cost or 0,
                current_price=current_price or new_price,
                has_position=has_position,
                triggered_history=[{"executed": condition.status == ConditionStatus.TRIGGERED}],
                active_triggers=active_triggers or [],
                override_reason=override_reason or user_reason,
            )
        elif condition_type == ConditionType.COST_PROTECTION:
            result = ConditionRules.validate_cost_protection_change(
                old_price=old_price,
                new_price=new_price,
                avg_cost=avg_cost or 0,
                active_triggers=active_triggers or [],
                override_reason=override_reason or user_reason,
                atr=atr,
                k_cost=k_cost,
            )
        else:
            # 软条件
            current_date = datetime.now().strftime("%Y-%m-%d")
            result = ConditionRules.validate_soft_condition_change(
                condition=condition,
                new_price=new_price,
                current_date=current_date,
                active_triggers=active_triggers or [],
                override_reason=override_reason or user_reason,
            )

        # 如果允许，执行更新
        if result.allowed:
            reason = override_reason or user_reason or result.message

            condition.price = round(new_price, 2)
            condition.modified_at = datetime.now().isoformat()
            condition.history.append(ConditionChange(
                old_price=old_price,
                new_price=round(new_price, 2),
                reason=reason,
                level=result.level,
                override_triggers=active_triggers or [],
            ))

            self.save_conditions(record)

        return result, record

    def set_condition(self, stock_name: str,
                      condition_type: ConditionType,
                      price: float,
                      action: str,
                      category: ConditionCategory,
                      expiry_days: int = None,
                      auto_link_cost: bool = False) -> ConditionsRecord:
        """设定新条件（初始化用）"""
        record = self.load_conditions(stock_name)
        if not record:
            record = ConditionsRecord(stock_name=stock_name)

        now = datetime.now().isoformat()
        expiry_date = None
        if expiry_days and category == ConditionCategory.SOFT:
            expiry_date = calculate_expiry_date(expiry_days)

        type_names = {
            ConditionType.TRAILING_STOP: "移动止损",
            ConditionType.COST_PROTECTION: "成本保护",
            ConditionType.TAKE_PROFIT_1: "止盈条件1",
            ConditionType.TAKE_PROFIT_2: "止盈条件2",
            ConditionType.ADD_POSITION: "加仓条件",
        }

        condition = Condition(
            type=condition_type,
            name=type_names.get(condition_type, condition_type.value),
            price=round(price, 2),
            action=action,
            category=category,
            expiry_date=expiry_date,
            auto_link_cost=auto_link_cost,
            created_at=now,
            modified_at=now,
            history=[ConditionChange(
                old_price=0,
                new_price=round(price, 2),
                reason="首次设定",
                level=ConditionLevel.LEVEL_1,
            )],
        )

        record.set(condition)
        self.save_conditions(record)
        return record

    def remove_condition(self, stock_name: str, condition_type: ConditionType) -> bool:
        """移除条件"""
        record = self.load_conditions(stock_name)
        if not record:
            return False

        condition = record.get(condition_type)
        if not condition:
            return False

        record.remove(condition_type)
        self.save_conditions(record)
        return True

    # ========== 事件条件管理 ==========

    def add_event_condition(self, stock_name: str,
                            event_type: str,
                            price: float,
                            action: str,
                            category: ConditionCategory,
                            expiry_days: int = None) -> tuple:
        """
        添加事件条件（支持同类型多实例）

        返回: (event_id, ConditionsRecord)
        """
        record = self.load_conditions(stock_name)
        if not record:
            return None, None

        now = datetime.now().isoformat()
        expiry_date = None
        if expiry_days and category == ConditionCategory.SOFT:
            expiry_date = calculate_expiry_date(expiry_days)

        # 事件条件名称映射
        event_names = {
            "profit_protect": "利润保护",
            "loss_protect": "亏损保护",
            "tech_break": "技术破位",
            "target_profit": "目标价止盈",
            "add_position": "加仓条件",
            "fundamental": "基本面事件",
            "market_risk": "市场风险",
        }

        condition = Condition(
            type=ConditionType.TRAILING_STOP,  # 事件条件统一用TRAILING_STOP作为基础类型
            name=event_names.get(event_type, event_type),
            price=round(price, 2),
            action=action,
            category=category,
            expiry_date=expiry_date,
            created_at=now,
            modified_at=now,
            history=[ConditionChange(
                old_price=0,
                new_price=round(price, 2),
                reason="首次设定（事件条件）",
                level=ConditionLevel.LEVEL_1,
            )],
        )

        event_id = record.add_event(condition)
        self.save_conditions(record)
        return event_id, record

    def remove_event_condition(self, stock_name: str, event_id: str) -> bool:
        """移除指定ID的事件条件"""
        record = self.load_conditions(stock_name)
        if not record:
            return False

        result = record.remove_event(event_id)
        if result:
            self.save_conditions(record)
        return result

    def trigger_event_condition(self, stock_name: str,
                                event_id: str,
                                trigger_price: float) -> Optional[ConditionsRecord]:
        """记录事件条件触发"""
        record = self.load_conditions(stock_name)
        if not record:
            return None

        event = record.get_event(event_id)
        if not event:
            return None

        event.status = ConditionStatus.TRIGGERED
        event.modified_at = datetime.now().isoformat()
        event.history.append(ConditionChange(
            old_price=event.price,
            new_price=trigger_price,
            reason=f"条件已触发（触发价: ¥{trigger_price:.2f}）",
            level=ConditionLevel.LEVEL_1,
        ))

        self.save_conditions(record)
        return record

    def trigger_condition(self, stock_name: str,
                          condition_type: ConditionType,
                          trigger_price: float) -> Optional[ConditionsRecord]:
        """记录条件触发"""
        record = self.load_conditions(stock_name)
        if not record:
            return None

        condition = record.get(condition_type)
        if not condition:
            return None

        condition.status = ConditionStatus.TRIGGERED
        condition.modified_at = datetime.now().isoformat()
        condition.history.append(ConditionChange(
            old_price=condition.price,
            new_price=trigger_price,
            reason=f"条件已触发（触发价: ¥{trigger_price:.2f}）",
            level=ConditionLevel.LEVEL_1,
        ))

        self.save_conditions(record)
        return record

    def expire_condition(self, stock_name: str,
                         condition_type: ConditionType) -> Optional[ConditionsRecord]:
        """记录条件过期"""
        record = self.load_conditions(stock_name)
        if not record:
            return None

        condition = record.get(condition_type)
        if not condition:
            return None

        condition.status = ConditionStatus.EXPIRED
        condition.modified_at = datetime.now().isoformat()
        condition.history.append(ConditionChange(
            old_price=condition.price,
            new_price=condition.price,
            reason="条件已过期（未触发）",
            level=ConditionLevel.LEVEL_1,
        ))

        self.save_conditions(record)
        return record

    # 成本保护缓冲系数：保护价 = 成本价 × (1 - BUFFER)
    # 作用是吸收日内正常波动，避免极轻仓被"回本就清仓"误伤
    COST_PROTECTION_BUFFER = 0.015

    # 建仓后短期保护缓冲：建仓后 BUILD_BUFFER_DAYS 个自然日内使用更宽的缓冲
    # 原因：建仓初期股价尚未脱离成本区，1.5% 缓冲不足以吸收正常日内波动，
    # 容易在建仓当天或次日就被清仓，本质上等于"买入即止损"
    BUILD_BUFFER = 0.03
    BUILD_BUFFER_DAYS = 3

    # 分批建仓期间的保护底线缓冲：保护价 = 最低买点 × (1 - BUILD_FLOOR_BUFFER)
    # 作用是分批建仓期间容忍计划内浮亏（如 ¥82 买入预期可能跌到 ¥80），
    # 同时在真正破位（跌破买点下沿 2%）时止损，避免"计划内浮亏"被误判为"判断错误"
    BUILD_FLOOR_BUFFER = 0.02

    def _is_within_build_buffer_period(self, stock_name: str) -> bool:
        """检查最近一次买入是否在建仓缓冲期内（BUILD_BUFFER_DAYS 个自然日内）"""
        try:
            history = self.storage.load_operations(stock_name)
            if not history or not history.operations:
                return False
            for op in reversed(history.operations):
                if op.type == "buy":
                    buy_time = datetime.fromisoformat(op.timestamp)
                    days_since = (datetime.now() - buy_time).days
                    return days_since < self.BUILD_BUFFER_DAYS
            return False
        except Exception:
            return False

    def sync_cost_protection(self, stock_name: str, avg_cost: float,
                             klines: List[dict] = None, atr: float = None,
                             k_cost: float = 2.0) -> Optional[ConditionsRecord]:
        """
        同步成本保护（buy/sell 后自动调用，或 atr-sync 命令调用）
        如果 auto_link_cost=True，自动更新成本保护价格。

        ATR 驱动（回测验证优于固定缓冲）：
        - 正常持仓：保护价 = 成本 − k_cost×ATR（不套只升不降，成本变了应重算）
        - 加成本×80% 底线，防 ATR 异常放大导致保护位跌穿成本 20%
        无 atr/klines 时退回旧固定缓冲（保证 buy 钩子零网络依赖）。

        三种场景：
        1. 分批建仓期间（存在未触发的 add_position 事件）：
           保护价 = min(ATR成本侧, 最低买点×0.98)——买点下沿底线是建仓计划保护，与波动率正交，不 ATR 化。
        2. 建仓缓冲期（最近一次买入在 BUILD_BUFFER_DAYS 天内，无 pending build）：
           保护价 = 成本 − k_cost×ATR（无 ATR 则退回成本×(1−3%)）。
        3. 正常持仓：保护价 = 成本 − k_cost×ATR（无 ATR 则退回成本×(1−1.5%)）。
        """
        record = self.load_conditions(stock_name)
        if not record:
            return None

        condition = record.get(ConditionType.COST_PROTECTION)
        if not condition:
            return None

        if not condition.auto_link_cost:
            return record

        # 计算 ATR（若提供 klines 未提供 atr）
        from paper_trading_v2.atr import compute_atr, ATR_K_COST
        atr_val = atr
        if atr_val is None and klines:
            atr_val = compute_atr(klines)
        kk = k_cost if k_cost is not None else ATR_K_COST

        # ATR 成本侧保护价（不套只升不降；成本变了应重算）
        atr_floor = None
        if atr_val is not None:
            atr_floor = round(avg_cost - kk * atr_val, 2)
            # 80% 底线：防 ATR 异常放大导致保护位跌穿成本 20%
            cost_floor_80 = round(avg_cost * 0.80, 2)
            if atr_floor < cost_floor_80:
                atr_floor = cost_floor_80

        # 检查是否存在未触发的 add_position 事件（分批建仓期间）
        pending_build_events = [
            e for e in record.events
            if e.name == "加仓条件" and e.status == ConditionStatus.ACTIVE
        ]

        # 检查是否在建仓缓冲期内
        in_build_buffer = self._is_within_build_buffer_period(stock_name)

        # 旧固定缓冲（无 ATR 时退回）
        if in_build_buffer:
            fixed_buffer = self.BUILD_BUFFER
            fixed_label = f"{self.BUILD_BUFFER*100:.1f}%(建仓缓冲{self.BUILD_BUFFER_DAYS}天)"
        else:
            fixed_buffer = self.COST_PROTECTION_BUFFER
            fixed_label = f"{self.COST_PROTECTION_BUFFER*100:.1f}%"
        fixed_price = round(avg_cost * (1 - fixed_buffer), 2)

        if pending_build_events:
            # 场景A：分批建仓期间 —— 保留买点下沿底线（与波动率正交），成本侧用 ATR
            min_buy_price = min(e.price for e in pending_build_events)
            build_floor = round(min_buy_price * (1 - self.BUILD_FLOOR_BUFFER), 2)
            cost_side = atr_floor if atr_floor is not None else fixed_price
            target_price = min(cost_side, build_floor)
            if atr_floor is not None:
                reason = (
                    f"分批建仓期间保护（ATR成本侧¥{atr_floor:.2f}=成本¥{avg_cost:.2f}−{kk}×ATR，"
                    f"买点底线¥{build_floor:.2f}=最低买点¥{min_buy_price:.2f}×0.98，取小值¥{target_price:.2f}）"
                )
            else:
                reason = (
                    f"分批建仓期间保护（成本¥{avg_cost:.2f}→{fixed_price:.2f}，"
                    f"最低买点¥{min_buy_price:.2f}→底线¥{build_floor:.2f}，取小值¥{target_price:.2f}）"
                )
        elif atr_floor is not None:
            # 场景B/C：有 ATR —— 纯 ATR 成本保护（建仓缓冲期与正常持仓统一用 ATR）
            target_price = atr_floor
            reason = f"ATR成本保护（成本¥{avg_cost:.2f} − {kk}×ATR¥{atr_val:.2f} = ¥{target_price:.2f}）"
        else:
            # 退回旧固定缓冲（无 ATR）
            target_price = fixed_price
            reason = f"自动同步持仓成本（缓冲{fixed_label}，成本¥{avg_cost:.2f}）"

        # 检查是否需要更新
        if abs(condition.price - target_price) < 0.01:
            return record  # 无需更新

        # 宽保护豁免（价值反转仓，2026-08-25 加入）：condition.name 含"宽保护"前缀时，
        # 保护价只宽不紧（ATR 收紧时保持旧宽保护价，波动放大时允许更宽）——
        # 防止每日 atr-sync 把 -12% 左侧容忍保护静默收紧回 ATR 值（2×ATR < 12% 即触发）
        if condition.name and "宽保护" in condition.name and condition.price is not None:
            if target_price > condition.price:
                target_price = condition.price
                reason = f"宽保护豁免（{reason}，不低于旧保护价¥{condition.price}）"

        old_price = condition.price
        condition.price = target_price
        condition.modified_at = datetime.now().isoformat()
        condition.history.append(ConditionChange(
            old_price=old_price,
            new_price=target_price,
            reason=reason,
            level=ConditionLevel.LEVEL_1,
        ))

        self.save_conditions(record)
        return record

    def sync_trailing_stop(self, stock_name: str, avg_cost: float,
                           klines: List[dict] = None, atr: float = None,
                           realtime_high: float = None,
                           k: float = None,
                           init_peak: str = "current",
                           reset_peak: bool = False,
                           current_price: float = None) -> Optional[ConditionsRecord]:
        """
        同步移动止损（ATR 驱动，只升不降）。由 `ptrade atr-sync` 命令调用。

        新止损 = max(旧止损, peak − k×ATR)，其中 peak = merge_peak(旧peak, klines, realtime_high)。
        - trailing_stop 硬只升不降（套 max）：ATR 变大时止损不降，只有 peak 上移才推高止损。
        - 与 cost_protection 语义不同（后者跟随成本重算，不套 max）。

        init_peak:
          - "current"（默认）: 首次(peak_price 为 None)用当前价初始化，避免历史 peak 导致止损突变清仓。
          - "historical": 用建仓以来区间最高价初始化（激进，立即收紧）。
        reset_peak: 重新建仓后重置 peak 为当前价（不继承上一轮 peak）。
        """
        record = self.load_conditions(stock_name)
        if not record:
            return None

        condition = record.get(ConditionType.TRAILING_STOP)
        if not condition:
            return None

        from paper_trading_v2.atr import compute_atr, merge_peak, ATR_K_TRAIL

        # 计算 ATR
        atr_val = atr if atr is not None else (compute_atr(klines) if klines else None)
        if atr_val is None:
            return record  # K线不足，跳过（不动止损）

        kk = k if k is not None else ATR_K_TRAIL

        # peak 初始化 / 重置 / 合并
        stale_peak_reset = False  # 标记是否因旧peak来自上一轮而重置
        if reset_peak or (init_peak == "current" and condition.peak_price is None):
            # 首次或重置：用当前价（保守，防突变）
            seed = current_price
            if seed is None and klines:
                seed = klines[-1].get("close")
            if seed is None:
                return record
            new_peak = seed
            peak_note = "当前价" if not reset_peak else "重置为当前价"
        else:
            # 合并：max(旧peak, 本轮区间最高high, realtime_high)
            # Bug #2 修复：只取本轮建仓以来的 K 线，剔除上一轮历史高点（如中科曙光 113）。
            # 若旧 peak 来自上一轮（本轮 K 线 high 全 < 旧 peak），视为 None 重新用当前价 seed，
            # 否则 max(旧peak, 本轮high) 永远被旧 peak 锁死，止损位虚高。
            round_klines, stale_peak = self._filter_klines_by_round(
                stock_name, klines or [], condition.peak_price)
            if stale_peak:
                # 旧 peak 是上一轮污染值 → 重新用当前价初始化（不走 max，允许止损下移）
                seed = current_price
                if seed is None and klines:
                    seed = klines[-1].get("close")
                if seed is None:
                    return record
                new_peak = seed
                peak_note = f"旧peak¥{condition.peak_price}来自上一轮，重置为当前价¥{seed}"
                stale_peak_reset = True
            else:
                new_peak = merge_peak(condition.peak_price, round_klines, realtime_high)
                if new_peak is None:
                    return record
                peak_note = f"max(旧peak¥{condition.peak_price}, 本轮区间high)"

        # ATR 移动止损：peak − k×ATR
        raw_stop = round(new_peak - kk * atr_val, 2)
        if stale_peak_reset:
            # 旧 peak 是污染值，止损下移是纠错而非趋势走弱 → 不套只升不降（同除权缩放语义）
            target = raw_stop
        else:
            target = max(condition.price, raw_stop)  # 硬只升不降

        peak_changed = (condition.peak_price is None) or (new_peak > condition.peak_price) or stale_peak_reset
        stop_changed = abs(condition.price - target) >= 0.01

        if not peak_changed and not stop_changed:
            return record

        if peak_changed:
            condition.peak_price = new_peak
        if stop_changed:
            old_price = condition.price
            condition.price = target
            only_up_note = "" if stale_peak_reset else "，只升不降"
            condition.history.append(ConditionChange(
                old_price=old_price,
                new_price=target,
                reason=f"ATR移动止损自动同步（peak¥{new_peak:.2f} − {kk}×ATR¥{atr_val:.2f} = ¥{raw_stop:.2f}{only_up_note}取¥{target:.2f}）",
                level=ConditionLevel.LEVEL_1,
            ))
        condition.modified_at = datetime.now().isoformat()

        self.save_conditions(record)
        return record

    def suspend_all(self, stock_name: str) -> Optional[ConditionsRecord]:
        """空仓时暂停所有硬条件（标准+事件）"""
        record = self.load_conditions(stock_name)
        if not record:
            return None

        for c in record.conditions.values():
            if c.category == ConditionCategory.HARD:
                c.status = ConditionStatus.SUSPENDED
                c.modified_at = datetime.now().isoformat()

        for e in record.events:
            if e.category == ConditionCategory.HARD:
                e.status = ConditionStatus.SUSPENDED
                e.modified_at = datetime.now().isoformat()

        self.save_conditions(record)
        return record

    def resume_all(self, stock_name: str) -> Optional[ConditionsRecord]:
        """重新建仓后恢复所有硬条件（标准+事件）"""
        record = self.load_conditions(stock_name)
        if not record:
            return None

        for c in record.conditions.values():
            if c.category == ConditionCategory.HARD and c.status == ConditionStatus.SUSPENDED:
                c.status = ConditionStatus.ACTIVE
                c.modified_at = datetime.now().isoformat()

        for e in record.events:
            if e.category == ConditionCategory.HARD and e.status == ConditionStatus.SUSPENDED:
                e.status = ConditionStatus.ACTIVE
                e.modified_at = datetime.now().isoformat()

        self.save_conditions(record)
        return record

    def check_expired(self, stock_name: str, current_date: str = None) -> List[Condition]:
        """检查并返回已过期条件（标准+事件）"""
        if not current_date:
            current_date = datetime.now().strftime("%Y-%m-%d")

        record = self.load_conditions(stock_name)
        if not record:
            return []

        expired = []
        # 标准条件
        for c in record.conditions.values():
            if c.category == ConditionCategory.SOFT and c.expiry_date:
                if current_date > c.expiry_date and c.status == ConditionStatus.ACTIVE:
                    c.status = ConditionStatus.EXPIRED
                    c.modified_at = datetime.now().isoformat()
                    expired.append(c)

        # 事件条件
        for e in record.events:
            if e.category == ConditionCategory.SOFT and e.expiry_date:
                if current_date > e.expiry_date and e.status == ConditionStatus.ACTIVE:
                    e.status = ConditionStatus.EXPIRED
                    e.modified_at = datetime.now().isoformat()
                    expired.append(e)

        if expired:
            self.save_conditions(record)

        return expired

    # ========== 止损触发检测（Bug #1 修复）==========

    # 跌破触发的条件类型/名称（现价 <= 触发价 即破位）
    DIRECTION_DOWN_TYPES = {
        ConditionType.TRAILING_STOP,
        ConditionType.COST_PROTECTION,
    }
    DIRECTION_DOWN_NAME_KEYWORDS = ("止损", "保护", "破位")

    # 涨破触发的条件类型/名称（现价 >= 触发价 即触发）
    DIRECTION_UP_TYPES = {
        ConditionType.TAKE_PROFIT_1,
        ConditionType.TAKE_PROFIT_2,
    }
    DIRECTION_UP_NAME_KEYWORDS = ("止盈", "目标")

    def _condition_direction(self, condition: Condition) -> str:
        """判定条件触发方向：'down'（跌破触发）或 'up'（涨破触发）。

        优先按名称关键字判定（事件条件 type 统一为 TRAILING_STOP，无法按 type 区分），
        标准条件按 ConditionType 兜底，避免中文名脆性。
        """
        name = condition.name or ""
        if any(k in name for k in self.DIRECTION_UP_NAME_KEYWORDS):
            return "up"
        if any(k in name for k in self.DIRECTION_DOWN_NAME_KEYWORDS):
            return "down"
        # 按 type 兜底（标准条件）
        if condition.type in self.DIRECTION_UP_TYPES:
            return "up"
        if condition.type in self.DIRECTION_DOWN_TYPES:
            return "down"
        # 默认按跌破（止损类是主流场景）
        return "down"

    def check_triggers(self, stock_name: str, current_price: float) -> List[dict]:
        """检测已破位的硬条件（只读，不改 status、不执行卖出）。

        遍历所有 status==ACTIVE 的硬条件（标准 + 事件），按方向比较 current_price 与
        condition.price，返回已触发清单。这是对"trigger-table 只反映手动标记、不反映
        实时破位"这一缺陷的补救——cron 在 atr-sync 后调用，把结果写进报告供 LLM 决策。
        """
        record = self.load_conditions(stock_name)
        if not record or current_price is None:
            return []

        breaches = []
        # 标准 hard 条件 + 事件 hard 条件，仅看 ACTIVE（跳过 SUSPENDED/EXPIRED/TRIGGERED）
        candidates = [c for c in record.list_hard() if c.status == ConditionStatus.ACTIVE]

        for cond in candidates:
            direction = self._condition_direction(cond)
            if direction == "down":
                is_breach = current_price <= cond.price
            else:  # up
                is_breach = current_price >= cond.price
            if not is_breach:
                continue
            amount = abs(current_price - cond.price)
            pct = (amount / cond.price * 100.0) if cond.price else 0.0
            breaches.append({
                "name": cond.name,
                "type": cond.type.value if hasattr(cond.type, "value") else str(cond.type),
                "trigger_price": cond.price,
                "current_price": round(current_price, 2),
                "direction": direction,
                "breach_amount": round(amount, 2),
                "breach_pct": round(pct, 2),
                "action": cond.action,
                "condition_id": cond.id,
            })

        # 按穿透幅度降序（最严重的在前）
        breaches.sort(key=lambda b: b["breach_amount"], reverse=True)
        return breaches

    # ========== 本轮建仓起点（Bug #2 修复：peak 只取本轮 K 线）==========

    def _current_build_round_start(self, stock_name: str) -> Optional[str]:
        """返回本轮建仓起点的 ISO timestamp（最近一次清仓后的首笔 buy），无则 None。

        用累计 buy qty − 累计 sell qty 追踪持仓量，降到 <=0 即清仓点；
        返回该清仓点之后的**第一笔 buy** 的 timestamp。
        若从未清仓过（建仓后一直持有），返回第一笔 buy 的 timestamp（本轮=唯一轮）。
        任何异常 / 无操作记录 → 返回 None（调用方退回现有行为）。

        注意 op.type 是字符串（Operation.Config.use_enum_values=True），按
        _is_within_build_buffer_period 的惯例用 == "buy"/"sell" 比较。
        """
        try:
            history = self.storage.load_operations(stock_name)
            if not history or not history.operations:
                return None

            ops = history.operations
            first_buy_ts = None
            last_clearance_ts = None  # 最近一次使持仓归零的 sell 的 timestamp
            holding = 0  # 累计持仓量（buy 加，sell 减）

            for op in ops:
                t = op.type
                if t == "buy" and op.quantity:
                    if first_buy_ts is None:
                        first_buy_ts = op.timestamp
                    holding += op.quantity
                elif t == "sell" and op.quantity:
                    holding -= op.quantity
                    if holding <= 0:
                        holding = 0
                        last_clearance_ts = op.timestamp  # 记录最近一次清仓时点

            if last_clearance_ts is None:
                # 从未清仓过 → 本轮就是唯一一轮，起点是首笔 buy
                return first_buy_ts

            # 找 last_clearance_ts 之后的第一笔 buy
            for op in ops:
                if op.type == "buy" and op.timestamp > last_clearance_ts:
                    return op.timestamp

            # 清仓后尚未重新建仓（当前应空仓，调用方一般不会走到这）
            return None
        except Exception:
            return None

    def _filter_klines_by_round(self, stock_name: str, klines: List[dict],
                                stored_peak: Optional[float]) -> tuple:
        """按本轮建仓起点过滤 K 线，并判断旧 peak_price 是否来自上一轮（需重置）。

        返回 (filtered_klines, stale_peak)：
        - filtered_klines：date >= round_start 的 K 线子集（round_start 为 None 时返回原列表）。
        - stale_peak：bool，当 round_start 存在且 stored_peak 非 None，但本轮过滤后的 K 线 high
          全部 < stored_peak 时为 True——说明 stored peak 来自上一轮历史高点（如中科曙光 113），
          应被视为 None 重新用 current 初始化，否则 max(旧peak, 本轮high) 永远被旧 peak 锁死。
        """
        round_start = self._current_build_round_start(stock_name)
        if round_start is None:
            return klines, False
        round_date = round_start[:10]
        filtered = [k for k in (klines or []) if k.get("date", "") >= round_date]

        stale_peak = False
        if stored_peak is not None:
            if not filtered:
                # 本轮建仓后尚无已收盘 K 线（如建仓当日盘中运行 atr-sync，K线只到昨日）：
                # stored peak 不可能来自本轮（本轮还没有 K 线），必为上一轮污染 → 重置。
                stale_peak = True
            else:
                round_high = max((k.get("high") for k in filtered if k.get("high") is not None), default=None)
                if round_high is not None and round_high < stored_peak:
                    stale_peak = True
        return filtered, stale_peak



    def format_markdown(self, stock_name: str, template: str = "all",
                        current_date: str = None) -> str:
        """格式化 markdown 输出"""
        record = self.load_conditions(stock_name)
        if not record:
            return f"> ⚠️ 未找到股票 '{stock_name}' 的条件记录。请先初始化。\n"

        if not current_date:
            current_date = datetime.now().strftime("%Y-%m-%d")

        # 先检查过期
        self.check_expired(stock_name, current_date)
        # 重新加载（可能已更新状态）
        record = self.load_conditions(stock_name)

        lines = []

        if template in ("trigger-table", "all"):
            active = [c for c in record.conditions.values() if c.status in (ConditionStatus.ACTIVE, ConditionStatus.SUSPENDED)]
            active_events = [e for e in record.events if e.status in (ConditionStatus.ACTIVE, ConditionStatus.SUSPENDED)]
            all_active = active + active_events
            lines.append(format_trigger_table(all_active, current_date))
            lines.append("")

        if template in ("audit-table", "all"):
            hard = record.list_hard()
            if hard:
                lines.append(format_audit_table(hard))
                lines.append("")

        if template in ("expired-table", "all"):
            expired = record.list_expired(current_date)
            if expired:
                lines.append("### 已失效条件（上期设定，已过期未触发）")
                lines.append("")
                lines.append("| 原条件 | 原触发价格 | 原失效日期 | 实际走势 | 结论 |")
                lines.append("|--------|-----------|-----------|----------|------|")
                for c in expired:
                    lines.append(
                        f"| {c.name} | ¥{c.price:.2f} | {c.expiry_date} | 未触及 | 条件已过期 |"
                    )
                lines.append("")
                lines.append("> 以上条件已过期，本期需基于最新市场面重新设定或进入复审流程。")
                lines.append("")

        if template in ("execution-check", "all"):
            lines.append("### 上期触发条件执行检查")
            lines.append("")
            lines.append("| 上期设定 | 价格 | 失效日期 | 当前日期 | 是否过期 | 是否触发 | 执行状态 |")
            lines.append("|----------|------|----------|----------|----------|----------|----------|")
            all_conditions = list(record.conditions.values()) + record.events
            for c in all_conditions:
                if c.category == ConditionCategory.HARD:
                    expiry = "持仓周期内"
                    is_expired = "否"
                else:
                    expiry = c.expiry_date or "-"
                    is_expired = "是" if c.status == ConditionStatus.EXPIRED else "否"

                is_triggered = "是" if c.status == ConditionStatus.TRIGGERED else "否"
                exec_status = {
                    ConditionStatus.ACTIVE: "未触发",
                    ConditionStatus.TRIGGERED: "已执行",
                    ConditionStatus.EXPIRED: "已失效",
                    ConditionStatus.SUSPENDED: "暂停",
                }.get(c.status, "未触发")

                lines.append(
                    f"| {c.name} | ¥{c.price:.2f} | {expiry} | {current_date} | {is_expired} | {is_triggered} | {exec_status} |"
                )
            lines.append("")
            lines.append("> 上期条件若已触发但未执行，本期必须声明\"上期条件已触发，建议立即执行\"")
            lines.append("")

        return "\n".join(lines)

    def format_pretty(self, stock_name: str) -> str:
        """格式化 pretty 输出（终端显示）"""
        record = self.load_conditions(stock_name)
        if not record:
            return f"❌ 未找到股票 '{stock_name}' 的条件记录\n"

        lines = [f"🔒 {stock_name} 触发条件", ""]

        all_conditions = list(record.conditions.values()) + record.events
        for c in all_conditions:
            category_icon = "🔒" if c.category == ConditionCategory.HARD else "🔧"
            status_icon = {
                ConditionStatus.ACTIVE: "⬜",
                ConditionStatus.TRIGGERED: "✅",
                ConditionStatus.EXPIRED: "⚠️",
                ConditionStatus.SUSPENDED: "⏸️",
            }.get(c.status, "⬜")

            expiry = "持仓周期内" if c.category == ConditionCategory.HARD else (c.expiry_date or "-")

            lines.append(
                f"  {category_icon} {c.name}: ¥{c.price:.2f} [{c.action}] "
                f"({status_icon} {c.status.value}) 有效期: {expiry}"
            )

            if c.history:
                last = c.history[-1]
                lines.append(f"     上次修改: {last.timestamp[:10]} ({last.level.value}) - {last.reason[:40]}")

        return "\n".join(lines)

    def format_json(self, stock_name: str) -> dict:
        """格式化 JSON 输出"""
        record = self.load_conditions(stock_name)
        if not record:
            return {"error": f"未找到股票 '{stock_name}' 的条件记录"}

        return json.loads(record.model_dump_json())
