"""条件规则引擎

提供条件数据模型和三级规则校验引擎：
- Level 1 (auto): 自动通行，规则内置允许
- Level 2 (reason): 理由说明，需填写修改理由
- Level 3 (override): 强制解锁，需勾选触发器+详细理由
- BLOCKED: 违规操作，不允许执行
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Dict, Any
from uuid import uuid4
from pydantic import BaseModel, Field


# ========== 枚举定义 ==========

class ConditionLevel(str, Enum):
    """条件修改等级"""
    LEVEL_1 = "auto"       # 自动通行
    LEVEL_2 = "reason"     # 理由说明
    LEVEL_3 = "override"   # 强制解锁
    BLOCKED = "blocked"    # 阻断


class ConditionType(str, Enum):
    """条件类型"""
    TRAILING_STOP = "trailing_stop"      # 移动止损
    COST_PROTECTION = "cost_protection"   # 成本保护
    TAKE_PROFIT_1 = "take_profit_1"      # 止盈条件1
    TAKE_PROFIT_2 = "take_profit_2"      # 止盈条件2
    ADD_POSITION = "add_position"        # 加仓条件


class ConditionCategory(str, Enum):
    """条件类别"""
    HARD = "hard"   # 硬条件（持仓周期内有效）
    SOFT = "soft"   # 软条件（有失效日期）


class ConditionStatus(str, Enum):
    """条件状态"""
    ACTIVE = "active"       # 有效
    TRIGGERED = "triggered" # 已触发
    EXPIRED = "expired"     # 已过期
    SUSPENDED = "suspended" # 暂停（空仓时）


# ========== 强制复审触发器 ==========

OVERRIDE_TRIGGERS = [
    "market_systemic_risk",    # 市场系统性风险
    "policy_change",           # 政策变化
    "fundamental_shock",       # 基本面突变
    "technical_breakdown",     # 技术破位
    "capital_flow_reversal",   # 资金流向逆转
    "view_direction_reverse",  # 观点方向逆转
]

OVERRIDE_TRIGGER_LABELS = {
    "market_systemic_risk": "市场系统性风险",
    "policy_change": "政策变化",
    "fundamental_shock": "基本面突变",
    "technical_breakdown": "技术破位",
    "capital_flow_reversal": "资金流向逆转",
    "view_direction_reverse": "观点方向逆转",
}

# ========== 数据模型 ==========

class ConditionChange(BaseModel):
    """条件变更记录"""
    old_price: float = Field(..., description="变更前价格")
    new_price: float = Field(..., description="变更后价格")
    reason: str = Field(..., description="变更理由")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="变更时间")
    level: ConditionLevel = Field(..., description="修改等级")
    override_triggers: List[str] = Field(default_factory=list, description="Level 3 解锁时勾选的触发器")


class EventConditionType(str, Enum):
    """事件条件类型（用于events列表，支持同类型多实例）"""
    PROFIT_PROTECT = "profit_protect"       # 利润保护
    LOSS_PROTECT = "loss_protect"           # 亏损保护
    TECH_BREAK = "tech_break"               # 技术破位
    TARGET_PROFIT = "target_profit"         # 目标价止盈
    ADD_POSITION = "add_position"           # 加仓条件
    FUNDAMENTAL = "fundamental"             # 基本面事件
    MARKET_RISK = "market_risk"             # 市场风险


class Condition(BaseModel):
    """单个条件"""
    id: str = Field(default_factory=lambda: str(uuid4())[:8], description="条件唯一ID")
    type: ConditionType = Field(..., description="条件类型")
    name: str = Field(..., description="显示名称")
    price: float = Field(..., description="触发价格")
    action: str = Field(..., description="触发动作")
    category: ConditionCategory = Field(..., description="条件类别")
    expiry_date: Optional[str] = Field(None, description="软条件失效日期")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间")
    modified_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="最后修改时间")
    status: ConditionStatus = Field(default=ConditionStatus.ACTIVE, description="状态")
    history: List[ConditionChange] = Field(default_factory=list, description="变更历史")
    auto_link_cost: bool = Field(default=False, description="是否自动跟随持仓成本")

    def to_table_row(self, current_date: str = None) -> dict:
        """转换为表格行数据"""
        status_icon = {
            ConditionStatus.ACTIVE: "⬜ 未触发",
            ConditionStatus.TRIGGERED: "✅ 已触发",
            ConditionStatus.EXPIRED: "⚠️ 已过期",
            ConditionStatus.SUSPENDED: "⏸️ 暂停",
        }.get(self.status, "⬜ 未触发")

        if self.category == ConditionCategory.HARD:
            expiry = "持仓周期内"
        else:
            expiry = self.expiry_date or "YYYY-MM-DD"

        # 修改等级显示
        last_change = self.history[-1] if self.history else None
        level_display = last_change.level.value if last_change else "auto"

        return {
            "条件类型": self.name,
            "触发价格": f"¥{self.price:.2f}",
            "触发动作": self.action,
            "类别": "🔒" if self.category == ConditionCategory.HARD else "🔧",
            "失效日期": expiry,
            "状态": status_icon,
            "修改等级": level_display.upper(),
        }

    def to_audit_row(self) -> dict:
        """转换为审计表行"""
        first_change = self.history[0] if self.history else None
        last_change = self.history[-1] if self.history else None

        return {
            "条件": self.name,
            "当前价格": f"¥{self.price:.2f}",
            "初始日期": first_change.timestamp[:10] if first_change else self.created_at[:10],
            "初始价格": f"¥{first_change.old_price:.2f}" if first_change else f"¥{self.price:.2f}",
            "上次修改": last_change.timestamp[:10] if last_change else self.created_at[:10],
            "修改等级": (last_change.level.value if last_change else "auto").upper(),
            "触发器": ", ".join([OVERRIDE_TRIGGER_LABELS.get(t, t) for t in (last_change.override_triggers if last_change else [])]) or "无",
        }


class ConditionsRecord(BaseModel):
    """一只股票的全部条件"""
    stock_name: str = Field(..., description="股票名称")
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="更新时间")
    conditions: Dict[str, Condition] = Field(default_factory=dict, description="条件字典（向后兼容，5种标准条件）")
    events: List[Condition] = Field(default_factory=list, description="事件条件列表（支持同类型多实例）")

    def get(self, condition_type: ConditionType) -> Optional[Condition]:
        """获取指定类型的标准条件"""
        return self.conditions.get(condition_type.value)

    def set(self, condition: Condition):
        """设置标准条件"""
        self.conditions[condition.type.value] = condition
        self.updated_at = datetime.now().isoformat()

    def remove(self, condition_type: ConditionType):
        """移除标准条件"""
        if condition_type.value in self.conditions:
            del self.conditions[condition_type.value]
            self.updated_at = datetime.now().isoformat()

    def add_event(self, condition: Condition) -> str:
        """添加事件条件，返回条件ID"""
        self.events.append(condition)
        self.updated_at = datetime.now().isoformat()
        return condition.id

    def remove_event(self, event_id: str) -> bool:
        """移除指定ID的事件条件"""
        for i, e in enumerate(self.events):
            if e.id == event_id:
                self.events.pop(i)
                self.updated_at = datetime.now().isoformat()
                return True
        return False

    def get_event(self, event_id: str) -> Optional[Condition]:
        """获取指定ID的事件条件"""
        for e in self.events:
            if e.id == event_id:
                return e
        return None

    def list_active(self) -> List[Condition]:
        """列出所有有效标准条件"""
        return [c for c in self.conditions.values() if c.status == ConditionStatus.ACTIVE]

    def list_active_events(self) -> List[Condition]:
        """列出所有有效事件条件"""
        return [e for e in self.events if e.status in (ConditionStatus.ACTIVE, ConditionStatus.SUSPENDED)]

    def list_hard(self) -> List[Condition]:
        """列出所有硬条件（标准+事件）"""
        standard = [c for c in self.conditions.values() if c.category == ConditionCategory.HARD]
        events = [e for e in self.events if e.category == ConditionCategory.HARD]
        return standard + events

    def list_soft(self) -> List[Condition]:
        """列出所有软条件（标准+事件）"""
        standard = [c for c in self.conditions.values() if c.category == ConditionCategory.SOFT]
        events = [e for e in self.events if e.category == ConditionCategory.SOFT]
        return standard + events

    def list_expired(self, current_date: str = None) -> List[Condition]:
        """列出已过期条件（标准+事件）"""
        if not current_date:
            current_date = datetime.now().strftime("%Y-%m-%d")

        expired = []
        # 标准条件
        for c in self.conditions.values():
            if c.status == ConditionStatus.EXPIRED:
                expired.append(c)
            elif c.category == ConditionCategory.SOFT and c.expiry_date:
                if current_date > c.expiry_date:
                    expired.append(c)
        # 事件条件
        for e in self.events:
            if e.status == ConditionStatus.EXPIRED:
                expired.append(e)
            elif e.category == ConditionCategory.SOFT and e.expiry_date:
                if current_date > e.expiry_date:
                    expired.append(e)
        return expired


class ValidationResult(BaseModel):
    """校验结果"""
    allowed: bool = Field(..., description="是否允许")
    level: ConditionLevel = Field(..., description="修改等级")
    message: str = Field(..., description="结果信息")
    requires_log: bool = Field(default=False, description="是否需要记录日志")
    requires_warning: bool = Field(default=False, description="是否生成警告")


# ========== 规则引擎 ==========

class ConditionRules:
    """条件修改规则引擎"""

    # ---- Level 1: 自动通行 ----

    # 成本保护缓冲系数：保护价 = 成本价 × (1 - BUFFER)
    COST_PROTECTION_BUFFER = 0.015

    @staticmethod
    def auto_sync_cost_protection(avg_cost: float) -> ValidationResult:
        """成本保护自动跟随持仓成本（含1.5%缓冲） — Level 1"""
        buffered = round(avg_cost * (1 - ConditionRules.COST_PROTECTION_BUFFER), 2)
        return ValidationResult(
            allowed=True,
            level=ConditionLevel.LEVEL_1,
            message=f"成本保护自动跟随持仓成本（成本¥{avg_cost:.2f}，保护价¥{buffered:.2f}）",
            requires_log=True,
            requires_warning=False,
        )

    @staticmethod
    def can_raise_trailing_stop(old_stop: float, new_stop: float,
                                 avg_cost: float, current_price: float) -> Optional[ValidationResult]:
        """浮盈状态下移动止损上移 — Level 1"""
        if new_stop > old_stop and current_price > avg_cost:
            return ValidationResult(
                allowed=True,
                level=ConditionLevel.LEVEL_1,
                message="浮盈状态下止损上移，符合只升不降规则",
                requires_log=True,
                requires_warning=False,
            )
        return None

    @staticmethod
    def can_reset_after_trigger(triggered: bool, executed: bool) -> Optional[ValidationResult]:
        """止损触发执行后重新设定 — Level 1"""
        if triggered and executed:
            return ValidationResult(
                allowed=True,
                level=ConditionLevel.LEVEL_1,
                message="上期止损已触发并执行，允许重新设定",
                requires_log=True,
                requires_warning=False,
            )
        return None

    # ---- Level 2: 理由说明 ----

    @staticmethod
    def can_lower_in_loss(old_stop: float, new_stop: float,
                          avg_cost: float, current_price: float) -> Optional[ValidationResult]:
        """浮亏状态下移止损 — Level 2（底线：成本80%）"""
        if new_stop < old_stop and current_price < avg_cost:
            min_allowed = round(avg_cost * 0.8, 2)
            if new_stop >= min_allowed:
                return ValidationResult(
                    allowed=True,
                    level=ConditionLevel.LEVEL_2,
                    message=f"浮亏状态允许下移止损，最低底线: ¥{min_allowed:.2f}（成本的80%）",
                    requires_log=True,
                    requires_warning=False,
                )
            else:
                return ValidationResult(
                    allowed=False,
                    level=ConditionLevel.BLOCKED,
                    message=f"❌ 禁止下移超过成本20%。最低允许: ¥{min_allowed:.2f}，请求: ¥{new_stop:.2f}",
                    requires_log=True,
                    requires_warning=True,
                )
        return None

    @staticmethod
    def can_remove_when_empty(has_position: bool) -> Optional[ValidationResult]:
        """空仓取消止损 — Level 2"""
        if not has_position:
            return ValidationResult(
                allowed=True,
                level=ConditionLevel.LEVEL_2,
                message="空仓状态，取消止损条件",
                requires_log=True,
                requires_warning=False,
            )
        return None

    @staticmethod
    def can_modify_soft_before_expiry(condition: Condition, current_date: str) -> Optional[ValidationResult]:
        """软条件到期前修改 — Level 2"""
        if condition.category == ConditionCategory.SOFT and condition.expiry_date:
            if current_date <= condition.expiry_date:
                return ValidationResult(
                    allowed=True,
                    level=ConditionLevel.LEVEL_2,
                    message=f"软条件尚未过期（有效期至 {condition.expiry_date}），修改需说明理由",
                    requires_log=True,
                    requires_warning=False,
                )
        return None

    # ---- Level 3: 强制解锁 ----

    @staticmethod
    def can_override_with_review(old_price: float, new_price: float,
                                   active_triggers: List[str],
                                   override_reason: str) -> ValidationResult:
        """重大变化时强制解锁 — Level 3"""
        if not active_triggers:
            return ValidationResult(
                allowed=False,
                level=ConditionLevel.BLOCKED,
                message="❌ 重大修改必须指定至少1个强制复审触发器",
                requires_log=True,
                requires_warning=True,
            )

        # 验证触发器是否合法
        invalid = [t for t in active_triggers if t not in OVERRIDE_TRIGGERS]
        if invalid:
            valid_list = ", ".join(OVERRIDE_TRIGGERS)
            return ValidationResult(
                allowed=False,
                level=ConditionLevel.BLOCKED,
                message=f"❌ 非法触发器: {invalid}。合法触发器: {valid_list}",
                requires_log=True,
                requires_warning=True,
            )

        if len(override_reason) < 20:
            return ValidationResult(
                allowed=False,
                level=ConditionLevel.BLOCKED,
                message=f"❌ 解锁理由不能少于20字（当前: {len(override_reason)}字）",
                requires_log=True,
                requires_warning=True,
            )

        trigger_labels = ", ".join([OVERRIDE_TRIGGER_LABELS.get(t, t) for t in active_triggers])
        return ValidationResult(
            allowed=True,
            level=ConditionLevel.LEVEL_3,
            message=f"⚠️ 强制复审解锁（触发器: {trigger_labels}）",
            requires_log=True,
            requires_warning=True,
        )

    # ---- 统一入口 ----

    @staticmethod
    def validate_trailing_stop_change(
        old_price: float,
        new_price: float,
        avg_cost: float,
        current_price: float,
        has_position: bool,
        triggered_history: List[dict] = None,
        active_triggers: List[str] = None,
        override_reason: str = ""
    ) -> ValidationResult:
        """统一验证：移动止损修改"""

        # 1. 检查是否有持仓
        if not has_position:
            result = ConditionRules.can_remove_when_empty(has_position)
            if result:
                return result

        # 2. 检查是否触发后重置
        if triggered_history:
            last = triggered_history[-1]
            if last.get("executed"):
                result = ConditionRules.can_reset_after_trigger(True, True)
                if result:
                    return result

        # 3. Level 1: 浮盈只升不降
        result = ConditionRules.can_raise_trailing_stop(old_price, new_price, avg_cost, current_price)
        if result:
            return result

        # 4. Level 2: 浮亏状态下移
        result = ConditionRules.can_lower_in_loss(old_price, new_price, avg_cost, current_price)
        if result and result.level == ConditionLevel.BLOCKED:
            return result
        if result:
            return result

        # 5. Level 3: 强制解锁
        if active_triggers:
            return ConditionRules.can_override_with_review(
                old_price, new_price, active_triggers, override_reason
            )

        # 6. 全部不满足 = 阻断
        return ValidationResult(
            allowed=False,
            level=ConditionLevel.BLOCKED,
            message=f"❌ 禁止修改移动止损（{old_price:.2f}→{new_price:.2f}）。"
                   f"当前价: ¥{current_price:.2f}, 成本: ¥{avg_cost:.2f}。"
                   f"浮盈时只升不降，浮亏时下移需理由，重大变化需解锁。",
            requires_log=True,
            requires_warning=True,
        )

    @staticmethod
    def validate_cost_protection_change(
        old_price: float,
        new_price: float,
        avg_cost: float,
        active_triggers: List[str] = None,
        override_reason: str = ""
    ) -> ValidationResult:
        """统一验证：成本保护修改"""

        # 成本保护 = 成本价 × (1 - 1.5%)，吸收日内波动
        expected_price = round(avg_cost * (1 - ConditionRules.COST_PROTECTION_BUFFER), 2)

        # 如果新价格等于期望保护价（带缓冲），自动通过
        if abs(new_price - expected_price) < 0.01:
            return ConditionRules.auto_sync_cost_protection(avg_cost)

        # 如果不等于期望保护价，需要解锁
        if active_triggers:
            return ConditionRules.can_override_with_review(
                old_price, new_price, active_triggers, override_reason
            )

        return ValidationResult(
            allowed=False,
            level=ConditionLevel.BLOCKED,
            message=f"❌ 成本保护必须等于 成本价×(1-1.5%) = ¥{expected_price:.2f}（成本¥{avg_cost:.2f}）。"
                   f"请求: ¥{new_price:.2f}，偏差 ¥{abs(new_price - expected_price):.2f}。"
                   f"如需修改，请使用 --override-trigger 解锁。",
            requires_log=True,
            requires_warning=True,
        )

    @staticmethod
    def validate_soft_condition_change(
        condition: Condition,
        new_price: float,
        current_date: str,
        active_triggers: List[str] = None,
        override_reason: str = ""
    ) -> ValidationResult:
        """统一验证：软条件修改"""

        # 1. 已过期：可以重新设定（Level 1）
        if condition.status == ConditionStatus.EXPIRED:
            return ValidationResult(
                allowed=True,
                level=ConditionLevel.LEVEL_1,
                message=f"条件已过期，允许重新设定",
                requires_log=True,
                requires_warning=False,
            )

        # 2. 未过期：需要理由（Level 2）
        if condition.expiry_date and current_date <= condition.expiry_date:
            return ValidationResult(
                allowed=True,
                level=ConditionLevel.LEVEL_2,
                message=f"软条件尚未过期（有效期至 {condition.expiry_date}），修改需说明理由",
                requires_log=True,
                requires_warning=False,
            )

        # 3. 有触发器：强制解锁（Level 3）
        if active_triggers:
            return ConditionRules.can_override_with_review(
                condition.price, new_price, active_triggers, override_reason
            )

        # 默认允许（新设定）
        return ValidationResult(
            allowed=True,
            level=ConditionLevel.LEVEL_1,
            message="新设定软条件",
            requires_log=True,
            requires_warning=False,
        )


# ========== 工具函数 ==========

def calculate_expiry_date(days: int = 7) -> str:
    """计算失效日期（默认7天后）"""
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def format_trigger_table(conditions: List[Condition], current_date: str = None) -> str:
    """格式化触发条件表（markdown）"""
    if not current_date:
        current_date = datetime.now().strftime("%Y-%m-%d")

    lines = [
        "### 本期触发条件表",
        "",
        "| 条件类型 | 触发价格 | 触发动作 | 类别 | 有效期 | 状态 | 修改等级 |",
        "|----------|----------|----------|------|--------|------|---------|",
    ]

    for c in conditions:
        row = c.to_table_row(current_date)
        lines.append(
            f"| {row['条件类型']} | {row['触发价格']} | {row['触发动作']} | "
            f"{row['类别']} | {row['失效日期']} | {row['状态']} | {row['修改等级']} |"
        )

    lines.extend([
        "",
        "> **类别说明**：🔒硬条件=持仓周期内有效，修改受规则约束；🔧软条件=从设定日起7个自然日失效",
        "> **条件来源**: 本期条件来自 `ptrade conditions` 系统记录",
        "> **校验状态**: ✅ 所有条件已通过规则引擎校验",
    ])

    return "\n".join(lines)


def format_audit_table(hard_conditions: List[Condition]) -> str:
    """格式化硬条件修改审计表"""
    lines = [
        "### 🔒 硬条件修改审计",
        "",
        "| 条件 | 当前价格 | 初始日期 | 初始价格 | 上次修改 | 修改等级 | 触发器 |",
        "|------|---------|---------|---------|---------|---------|--------|",
    ]

    for c in hard_conditions:
        row = c.to_audit_row()
        lines.append(
            f"| {row['条件']} | {row['当前价格']} | {row['初始日期']} | "
            f"{row['初始价格']} | {row['上次修改']} | {row['修改等级']} | {row['触发器']} |"
        )

    lines.extend([
        "",
        "> **系统状态**: ✅ 所有硬条件修改均符合规则",
    ])

    return "\n".join(lines)
