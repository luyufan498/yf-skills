"""条件管理器

提供条件的增删改查、规则校验、与交易操作联动等功能。
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from paper_trading.conditions import (
    Condition, ConditionType, ConditionCategory, ConditionStatus,
    ConditionLevel, ConditionsRecord, ConditionChange,
    ConditionRules, ValidationResult,
    format_trigger_table, format_audit_table, calculate_expiry_date,
    OVERRIDE_TRIGGERS,
)
from paper_trading.storage import JsonStorage
from paper_trading.models import Account


class ConditionsManager:
    """条件管理器"""

    def __init__(self, storage: JsonStorage = None):
        self.storage = storage or JsonStorage()

    def _get_conditions_file(self, stock_name: str) -> Path:
        """获取条件文件路径"""
        account_dir = self.storage._get_account_dir(stock_name)
        return account_dir / "conditions.json"

    def load_conditions(self, stock_name: str) -> Optional[ConditionsRecord]:
        """加载条件记录"""
        file_path = self._get_conditions_file(stock_name)

        if not file_path.exists():
            return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return ConditionsRecord.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error loading conditions for {stock_name}: {e}")
            return None

    def save_conditions(self, record: ConditionsRecord) -> Path:
        """保存条件记录"""
        file_path = self._get_conditions_file(record.stock_name)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        record.updated_at = datetime.now().isoformat()

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(record.model_dump_json(ensure_ascii=False, indent=2))

        return file_path

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
                         user_reason: str = "") -> tuple:
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

    # 分批建仓期间的保护底线缓冲：保护价 = 最低买点 × (1 - BUILD_FLOOR_BUFFER)
    # 作用是分批建仓期间容忍计划内浮亏（如 ¥82 买入预期可能跌到 ¥80），
    # 同时在真正破位（跌破买点下沿 2%）时止损，避免"计划内浮亏"被误判为"判断错误"
    BUILD_FLOOR_BUFFER = 0.02

    def sync_cost_protection(self, stock_name: str, avg_cost: float) -> Optional[ConditionsRecord]:
        """
        同步成本保护（buy/sell 后自动调用）
        如果 auto_link_cost=True，自动更新成本保护价格。

        两种场景：
        1. 正常持仓（无未触发的 add_position 事件）：保护价 = 成本价 × (1 - 1.5%)
        2. 分批建仓期间（存在未触发的 add_position 事件）：
           保护价 = min(成本价 × 0.985, 最低买点 × 0.98)
           建仓期间容忍计划内浮亏，建仓完成后自动切换回正常成本保护。
        """
        record = self.load_conditions(stock_name)
        if not record:
            return None

        condition = record.get(ConditionType.COST_PROTECTION)
        if not condition:
            return None

        if not condition.auto_link_cost:
            return record

        # 检查是否存在未触发的 add_position 事件（分批建仓期间）
        # 事件条件的 type 字段统一为 TRAILING_STOP，需通过 name 字段识别 add_position
        pending_build_events = [
            e for e in record.events
            if e.name == "加仓条件" and e.status == ConditionStatus.ACTIVE
        ]

        cost_based_price = round(avg_cost * (1 - self.COST_PROTECTION_BUFFER), 2)

        if pending_build_events:
            # 分批建仓期间：取 min(成本×0.985, 最低买点×0.98)
            min_buy_price = min(e.price for e in pending_build_events)
            build_floor = round(min_buy_price * (1 - self.BUILD_FLOOR_BUFFER), 2)
            target_price = min(cost_based_price, build_floor)
            reason = (
                f"分批建仓期间保护（成本¥{avg_cost:.2f}→{cost_based_price:.2f}，"
                f"最低买点¥{min_buy_price:.2f}→底线¥{build_floor:.2f}，取小值¥{target_price:.2f}）"
            )
        else:
            # 正常持仓：成本 × (1 - 1.5%)
            target_price = cost_based_price
            reason = f"自动同步持仓成本（缓冲{self.COST_PROTECTION_BUFFER*100:.1f}%，成本¥{avg_cost:.2f}）"

        # 检查是否需要更新
        if abs(condition.price - target_price) < 0.01:
            return record  # 无需更新

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

    # ========== 格式化输出 ==========

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
