"""Watchlist — 池（关注名单）管理，三档策略 + NEWS 消息组缓冲 + L1 人工锁"""
from datetime import datetime
from paper_trading_v2.db import get_connection, migrate_db

# NEWS = 消息组 L2 信号缓冲（方案 2.5 组×层模型）：无交易权限，收编扫描/晨审落库
STRATEGIES = ("L1", "L2", "L3", "NEWS")


class Watchlist:
    """池：动态关注名单。L1 人工锁定（AI 无权自动移出/降级）。"""

    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    def add(self, stock, code=None, strategy="L2", source="agent", reason="", pin=None,
            event_key=None, news_kind=None, force=False):
        """入池/调整档位。档位可自由设置（L1=持仓段由 allocate 联动，也可手动指定）；
        pin 为独立保护标记（pin=1 禁止删除但允许降级）。
        event_key/news_kind：消息组收编参数（NEWS 缓冲行落 G3 事件键与六类词表打标，
        watchlog.event_key 是 G3 归并的键源；pool.event_key 为 stock↔事件权威落点）。

        NEWS 入池硬门（方案 第四.8，落库层实现=CLI 全路径覆盖，prompt 只是第一道软约束）：
        - 守卫 A（抢档）：该股有技术组 open 段 → 拒绝 NEWS 收编。技术组 open 段持有者
          走 ④动量/甜点加仓，不走消息组（同票双组暴露的 CLI 入口封堵）。
        - 守卫 B（重复入池/断链）：该股 NEWS 行 active 且已挂任一活跃槽 → 拒绝再次
          watchlist-add NEWS 换键（断 G3 归并链）。二波走 sleeve-open --add/#bN 派生波。
        - force=True（CLI --force）跳过 A/B，watchlog reason 追加 [force] 供审计。
        - fail-closed：守卫查询异常自然上抛拒绝入池（不 except 吞）。
        放行态（无段无槽：L2/裸 NEWS 行、M3 晨审换键）行为逐字节不变。
        """
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy 必须是 {STRATEGIES}")
        conn = self._conn()
        try:
            if strategy == 'NEWS' and not force:
                self._news_guards(conn, stock)
            elif strategy == 'NEWS':
                reason = (reason + ' ' if reason else '') + \
                    "[force 跳过 NEWS 入池硬门（第四.8 守卫A/B），人工裁决留痕]"
            with conn:
                existing = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
                if existing:
                    conn.execute("UPDATE pool SET code=COALESCE(?,code), strategy=?, pool_status='active', "
                                 "entered_at=COALESCE(entered_at, ?), "
                                 "event_key=COALESCE(?, event_key) WHERE stock=?",
                                 (code, strategy, datetime.now().isoformat(),
                                  event_key, stock))
                    if pin is not None:
                        conn.execute("UPDATE pool SET pin=? WHERE stock=?", (1 if pin else 0, stock))
                    action = "set_strategy"
                    from_str = existing['strategy']
                else:
                    conn.execute("INSERT INTO pool (stock, code, strategy, pool_status, "
                                 "refresh_cadence, entered_at, event_key, pin) "
                                 "VALUES (?,?,?,'active',?,?,?,?)",
                                 (stock, code, strategy,
                                  'daily' if strategy in ('L1', 'L2') else 'event',
                                  datetime.now().isoformat(), event_key, 1 if pin else 0))
                    action = "add"
                    from_str = None
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source, event_key, news_kind) "
                             "VALUES (?,?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), action, stock, from_str,
                              strategy, reason, source, event_key, news_kind))
            return True
        finally:
            conn.close()

    def _news_guards(self, conn, stock):
        """NEWS 入池硬门（第四.8）。命中即 ValueError；查询异常自然上抛（fail-closed）。

        实测槽活跃态=('open','partial')（SLOT_ACTIVE），另含 'migrated'（方案 2.2
        占用元组含 migrated；迁移槽成员已转 L1 段、pool 行不再 NEWS，实际由守卫 A
        拦截，列在此仅收紧）。判定用 event_slot_members 精确连接（等价于
        members_json LIKE 但无同名子串误伤）。
        """
        n_tech = conn.execute(
            "SELECT COUNT(*) FROM position WHERE stock=? AND status='open' "
            "AND COALESCE(strategy,'')!='NEWS'", (stock,)).fetchone()[0]
        if n_tech:
            raise ValueError(
                f"{stock} 有 open 段禁 NEWS 收编（第四.8）：技术组 open 段持有者走 "
                f"④动量/甜点加仓，不走消息组；确需收编先 release 技术段或 --force 留痕")
        existing = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
        if existing and existing['strategy'] == 'NEWS' and \
                existing['pool_status'] == 'active':
            in_slot = conn.execute(
                "SELECT es.event_key FROM event_slots es "
                "JOIN event_slot_members m ON m.event_key=es.event_key "
                "WHERE m.stock=? AND es.status IN ('open','partial','migrated') "
                "ORDER BY es.opened_at DESC LIMIT 1", (stock,)).fetchone()
            if in_slot:
                raise ValueError(
                    f"{stock} 已在槽 {in_slot[0]}（活跃事件槽成员），禁 watchlist-add NEWS "
                    f"重复入池/换键断链；二波走 sleeve-open --add 派生波（#bN 新键）"
                    f"（第四.8），确需改键 --force 留痕")

    def set_pin(self, stock, pin: bool, source="agent", reason=""):
        """设置/取消 pin 保护（独立于档位：pin 只禁止删除，不限制升降级）。"""
        conn = self._conn()
        try:
            row = conn.execute("SELECT strategy, pin FROM pool WHERE stock=?", (stock,)).fetchone()
            if not row:
                raise ValueError(f"{stock} 不在池中")
            if not pin and row['pin'] and source == 'agent':
                # 取消 pin 需人工确认（防 agent 误删保护）
                raise ValueError("取消 pin 需人工确认（source=manual）")
            with conn:
                conn.execute("UPDATE pool SET pin=? WHERE stock=?", (1 if pin else 0, stock))
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source) VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'set_pin', stock, row['strategy'],
                              None, reason, source))
            return True
        finally:
            conn.close()

    def remove(self, stock, source="agent", reason="", archive=False):
        """移出池。pin=1 的股票禁止删除（可降级但不可移除）。

        archive=True（sleeve-m1 新增 --archive 路径）：档案化终态——pool_status='archived'
        + archived_at，不触发旧 removed 语义（清仓不回池，回池要新证据，方案 2.6b）。
        archive=False（默认）→ 旧行为逐字节不变（pool_status='removed'）。
        """
        conn = self._conn()
        try:
            row = conn.execute("SELECT strategy, pin FROM pool WHERE stock=?", (stock,)).fetchone()
            if not row:
                raise ValueError(f"{stock} 不在池中")
            if row['pin']:
                raise ValueError(f"{stock} 有 pin 保护（名单锁定），禁止删除；可降级到 L3 观察")
            with conn:
                if archive:
                    conn.execute("UPDATE pool SET pool_status='archived', archived_at=?, "
                                 "exit_reason=? WHERE stock=?",
                                 (datetime.now().isoformat(), reason, stock))
                    action = 'archive'
                else:
                    conn.execute("UPDATE pool SET pool_status='removed', exit_reason=? WHERE stock=?",
                                 (reason, stock))
                    action = 'remove'
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source) VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), action, stock, row['strategy'],
                              None, reason, source))
            return True
        finally:
            conn.close()

    def list(self, status="active"):
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM pool WHERE pool_status=? ORDER BY strategy, stock",
                                (status,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def log(self, stock=None, days=30, limit=50):
        """名单变更审计日志（入池/出池/升降级历史），按时间倒序。"""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._conn()
        try:
            q = "SELECT * FROM watchlog WHERE timestamp >= ?"
            params = [cutoff]
            if stock:
                q += " AND stock=?"
                params.append(stock)
            q += " ORDER BY id DESC LIMIT ?"
            params.append(str(limit))
            return [dict(r) for r in conn.execute(q, params).fetchall()]
        finally:
            conn.close()

    def get(self, stock):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
