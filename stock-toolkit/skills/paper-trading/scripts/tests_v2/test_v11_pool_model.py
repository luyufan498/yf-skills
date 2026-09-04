"""v11 主池账务改造回归锁（先红后绿）

模型（方案 v11-pool-model-20260902）：
- 物理层：pool.free = total − Σ实际持仓成本 − Σ段滞留 cash（钱只在 buy 瞬间出池、sell 回款直接回池）
- 计划层：Σ段预算=承诺（可超售）；承诺率>80% → 新段须轮换伴配（--rotation-out）
- floor 20%×total 只在入场 buy 单点生效；rotation/topup/manual 豁免
- allocate 信封化：只动 budget 不搬 cash；段 cash 不足 buy 自动拨付（pool_grant）
- sell 回款直接回 pool（pool_return），段.cash 不留存
- pool_publicize.py 一次性迁移：非 NEWS open 段 cash→pool free，幂等
- NEWS 池/槽/6 pending 回归锁：零改动零行为变化（红线）

装配纪律：_make_direct_seg 保持账本诚实（建段即从 free 扣 cash+成本），
分场景再 _set_free 的必须与 Σ成本口径一致——测试自己先把恒等式做对，才有资格锁账。
红线自检：本文件全部走 pytest tmp workspace，生产库零接触。
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch

from paper_trading_v2.db import get_connection, migrate_db


# ---------- fixtures / helpers ----------

@pytest.fixture(autouse=True)
def no_network(ws):
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        yield


@pytest.fixture
def env(ws, monkeypatch):
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    return ws


@pytest.fixture
def pools(env):
    """主池 10M（不建消息池的组用；floor=2M）。"""
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(10_000_000)
    return m


class _PI:
    def __init__(self, p):
        self.current_price = p
        self.open_price = p
        self.pre_close = p


def _conn(env):
    return get_connection(env / 'master_pool.db')


def _trader(env):
    from paper_trading_v2.trading import PaperTrader
    from paper_trading_v2.storage import SqlStorage
    return PaperTrader(storage=SqlStorage(env / 'master_pool.db'))


def _free(env, ledger='pool_ledger'):
    conn = _conn(env)
    try:
        return conn.execute(f"SELECT free FROM {ledger} WHERE id=1").fetchone()[0]
    finally:
        conn.close()


def _seg_row(env, stock, strategy_not_news=True):
    conn = _conn(env)
    try:
        q = "SELECT * FROM position WHERE stock=? AND status='open'"
        if strategy_not_news:
            q += " AND COALESCE(strategy,'')!='NEWS'"
        r = conn.execute(q + " ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _make_direct_seg(env, stock, budget, cash, cost_qty=0, cost_price=10.0,
                     strategy='L1', code='sh1', entry_mode='normal', source='agent'):
    """段直建（绕 allocate 门——测 entry buy 单点/迁移用）。账本诚实：建段即在 pool.free
    扣 cash+成本（这笔钱物理上不在池里）；trades 纯现金流行（一笔 buy 成本行）。
    entry_mode/source 列 v10 迁移前不存在，按 PRAGMA 动态拼列（旧代码上红在断言/门缺失）。"""
    conn = _conn(env)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(position)").fetchall()}
        data = {'stock': stock, 'code': code, 'strategy': strategy, 'status': 'open',
                'budget': budget, 'topup_total': 0, 'opened_at': '2026-09-02T09:00:00',
                'cash': cash, 'fifo_index': -1, 'fifo_offset': 0}
        if 'entry_mode' in cols:
            data['entry_mode'] = entry_mode
        if 'source' in cols:
            data['source'] = source
        keys = ', '.join(data)
        marks = ', '.join('?' * len(data))
        cur = conn.execute(
            f"INSERT INTO position ({keys}) VALUES ({marks})", list(data.values()))
        seg_id = cur.lastrowid
        cost = cost_qty * cost_price
        if cost_qty:
            from tests_v2.v9_helpers import insert_buy
            insert_buy(conn, seg_id, cost_qty, cost_price, code=code)
        cur2 = conn.execute("UPDATE pool_ledger SET free=free-? WHERE id=1 AND free>=?",
                            (cash + cost, cash + cost))
        if cur2.rowcount == 0:
            raise AssertionError(f"fixture 装配错误：free 不足以诚实扣减 ¥{cash + cost}")
        conn.commit()
        return seg_id
    finally:
        conn.close()


def _set_free(env, free):
    conn = _conn(env)
    conn.execute("UPDATE pool_ledger SET free=? WHERE id=1", (free,))
    conn.commit()
    conn.close()


def _audit_rows(env, action, stock=None):
    conn = _conn(env)
    try:
        q = "SELECT * FROM audit WHERE action=?"
        args = [action]
        if stock:
            q += " AND stock=?"
            args.append(stock)
        return [dict(r) for r in conn.execute(q + " ORDER BY id", args).fetchall()]
    finally:
        conn.close()


def _commitment_env(env, pools, specs, buy_price=10.0):
    """满仓式承诺场景（v11 世界真态：信封内无滞留现金——公开化后 cash≈0）：
    specs=[(stock, budget=cost, ...)]，每段 budget=cost、cash=0（全仓持仓），
    free 自动 = total − Σcost（helper 逐段扣）。"""
    for stock, budget, cost in specs:
        _make_direct_seg(env, stock, budget, 0.0,
                         cost_qty=int(cost / buy_price), cost_price=buy_price)


# ============ a) 真实占用>2/3 轮换门（9/3 用户裁决：门基于真实值非承诺值） ============

def test_a1_real_gate_rejects_normal_allocate(pools, env):
    """真实占用率 Σ净持仓成本/total > 66.7%（预留 1/3）且 normal 且非 manual →
    allocate 拒，话术引用轮换评估（§8.2.3）。成本 8.05M=80.5%>2/3。"""
    _commitment_env(env, pools, [('石化甲', 3_000_000, 3_000_000),
                                 ('石化乙', 3_000_000, 3_000_000),
                                 ('石化丙', 2_050_000, 2_050_000)])   # 真实 80.5%>66.7%
    with pytest.raises(ValueError, match='真实占用|轮换'):
        pools.allocate('新候选', 300_000, reason='9/2 式新入场')


def test_a5_real_rate_below_gate_passes(pools, env):
    """边界锁·下沿：真实占用 65% ≤ 66.7% → normal 直放（不看承诺率脸色）。
    预算 9M（承诺 90%）但成本 6.5M——门基于真实值（9/3 裁决核心命题）。"""
    _make_direct_seg(env, '重仓甲', 4_500_000, 0.0, cost_qty=325_000)   # 成本 3.25M
    _make_direct_seg(env, '重仓乙', 4_500_000, 0.0, cost_qty=325_000)   # 成本 3.25M
    pools.allocate('直放票', 500_000, reason='真实 65%≤2/3')
    assert _seg_row(env, '直放票') is not None


def test_a6_real_rate_above_gate_rejects(pools, env):
    """边界锁·上沿：真实占用 68% > 66.7% → 拒（阈值改动会被此对锁暴露）。
    成本 6.8M，free 3.2M > floor 2M——证明拦的是占用门不是 floor。"""
    _make_direct_seg(env, '顶仓甲', 3_400_000, 0.0, cost_qty=340_000)   # 成本 3.4M
    _make_direct_seg(env, '顶仓乙', 3_400_000, 0.0, cost_qty=340_000)   # 成本 3.4M
    with pytest.raises(ValueError, match='真实占用|轮换'):
        pools.allocate('撞门票', 400_000, reason='真实 68%>2/3')


def test_a2_envelope_allocate_moves_no_cash(pools, env):
    """信封化：normal allocate（承诺率<80%）只动 budget，不动 pool.free、段 cash=0。"""
    _make_direct_seg(env, '甲', 1_000_000, 0.0, cost_qty=30_000)   # 承诺 10%（成本 30 万）
    free_before = _free(env)
    pools.allocate('信封票', 1_000_000, reason='信封化')
    assert _free(env) == free_before, "allocate 不再从 pool 搬 cash（信封化）"
    seg = _seg_row(env, '信封票')
    assert seg['budget'] == 1_000_000
    assert (seg['cash'] or 0) == 0, "段.cash=0 常态（预算≠现金）"


def test_a3_rotation_companion_allows_and_writes_sellpoint(pools, env, monkeypatch):
    """承诺率>80% + --rotation-out 合法技术 open 段 → 放行；audit rotation_out 行
    + 卖出 watchpoint（kv_store('watch_points')，mode=sell，price=现价×0.99）。
    （2026-09-04 改造：ROTATION_EXIT 事件退役，轮换卖单直写 watchpoint sell 点。）"""
    tasks_db = env / 'tasks.db'
    monkeypatch.setenv('STOCK_TASKS_DB', str(tasks_db))
    _commitment_env(env, pools, [('换出甲', 3_000_000, 3_000_000),
                                 ('换出乙', 3_000_000, 3_000_000),
                                 ('换出丙', 2_050_000, 2_050_000)])
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_PI(10.0)):
        pools.allocate('轮换入', 400_000, reason='高价值轮换',
                       entry_mode='rotation', rotation_out='换出甲')
    seg = _seg_row(env, '轮换入')
    assert seg is not None
    ro = _audit_rows(env, 'rotation_out', '换出甲')
    assert len(ro) == 1, f"audit rotation_out 行缺失：{ro}"
    assert ro[0]['reason'] and '轮换入' in ro[0]['reason']
    assert tasks_db.exists(), "task_bus kv 库未建（跨包写入失败未降级留痕？）"
    tconn = sqlite3.connect(str(tasks_db))
    tconn.row_factory = sqlite3.Row
    row = tconn.execute("SELECT value FROM kv_store WHERE key='watch_points'").fetchone()
    ev = tconn.execute("SELECT COUNT(*) c FROM task_events WHERE type='ROTATION_EXIT'").fetchone()['c']
    tconn.close()
    assert ev == 0, "ROTATION_EXIT 已退役，不得再生产"
    assert row, "watch_points kv 未写（跨包写入失败未降级留痕？）"
    pts = json.loads(row['value'])
    assert '换出甲' in pts, pts
    p = pts['换出甲'][0]
    assert p['mode'] == 'sell', p
    assert p['price'] == pytest.approx(9.9), f"price 应=现价10×0.99=9.9，实得 {p['price']}"
    assert '轮换出池换入轮换入限价卖' in p['note'], p
    assert p.get('code'), "watchpoint 须带 code（心跳取价用）"


def test_a4_rotation_out_fake_code_rejected(pools, env):
    """伪造 rotation-out CODE（无技术 open 段）→ 拒。"""
    _commitment_env(env, pools, [('真段甲', 3_000_000, 3_000_000),
                                 ('真段乙', 3_000_000, 3_000_000),
                                 ('真段丙', 2_050_000, 2_050_000)])
    with pytest.raises(ValueError, match='不存在|无.*open 段|无.*open'):
        pools.allocate('轮换入伪', 400_000, reason='伪造伴配',
                       entry_mode='rotation', rotation_out='幽灵票')


# ============ b) 假稀缺场景复刻（9/2 长鑫式：free<floor 且承诺>80%） ============

def _changxin_env(env, pools):
    """9/2 复刻：承诺 8.68M（86.8%>80%），Σ成本 8.6M → free=1.4M < floor 2M。
    返回第四个段（空信封，供 entry buy 试门）。"""
    _commitment_env(env, pools, [('鑫甲', 2_800_000, 2_800_000),
                                 ('鑫乙', 2_800_000, 2_800_000),
                                 ('鑫丙', 2_800_000, 2_800_000)])
    _make_direct_seg(env, '鑫仓', 280_000, 0.0, cost_qty=20_000)   # 20 万成本
    assert _free(env) == pytest.approx(10_000_000 - 8_600_000)
    return _seg_row(env, '鑫仓')


def test_b1_normal_entry_buy_floor_reject(pools, env):
    """free 1.4M < floor 2M 且承诺 86.8%>80%：normal entry buy（段首仓）→ 拒
    （真实口径话术：假稀缺判据 free−cost<floor 在真实现金下复现，但话术不再冤枉预算）。"""
    _changxin_env(env, pools)
    # 鑫仓已有持仓（20 万成本）→ 清掉流水令其空段首仓语义（直建成本行仅造 free 水位）
    conn = _conn(env)
    seg = _seg_row(env, '鑫仓')
    conn.execute("DELETE FROM trades WHERE account_id=?", (seg['id'],))
    conn.execute("UPDATE pool_ledger SET free=free+200000 WHERE id=1")   # 成本行撤回→free回1.6M
    conn.commit()
    conn.close()
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        # 买 50 万：1.6M−500k=1.1M < floor 2M → 拒
        with pytest.raises(ValueError, match='真实|floor|穿底'):
            _trader(env).buy_stock('鑫仓', quantity=50_000, note='假稀缺入场')
    # 旧行为（红）=段内无钱直接"资金不足"或买成；新行为（绿）=floor 话术拒且分文不动
    assert _free(env) == pytest.approx(1_600_000), "floor 拒后池资金零移动"
    assert len(_audit_rows(env, 'pool_grant', '鑫仓')) == 0, "floor 拒先于拨付"


def test_b2_rotation_entry_buy_floor_exempt(pools, env, monkeypatch):
    """同场景：entry_mode=rotation 段 → floor 豁免放行（自动拨付差额，pool_grant）。"""
    monkeypatch.setenv('STOCK_TASKS_DB', str(env / 'tasks.db'))
    _changxin_env(env, pools)
    pools.allocate('轮换鑫', 500_000, reason='换入', source='agent', code='sh1',
                   entry_mode='rotation', rotation_out='鑫甲')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('轮换鑫', quantity=40_000, note='轮换入首仓')   # 40万
    # 承诺门已过 80%，free=1.4M<floor：rotation 豁免 → 只扣真实成交额
    assert _free(env) == pytest.approx(1_000_000), "floor 豁免：只扣真实成交额"
    seg = _seg_row(env, '轮换鑫')
    assert (seg['cash'] or 0) == pytest.approx(0.0), "拨付即消耗，段 cash 不留存"
    assert len(_audit_rows(env, 'pool_grant', '轮换鑫')) == 1


def test_b3_rotation_obligation_over_one_rejected(pools, env, monkeypatch):
    """未平仓轮换义务≤1：第二笔 rotation 段入场时第一义务未销账 → 拒；
    卖换出票（sell 回池=卖出义务销账）后放行。"""
    monkeypatch.setenv('STOCK_TASKS_DB', str(env / 'tasks.db'))
    _commitment_env(env, pools, [('义甲', 2_800_000, 2_800_000),
                                 ('义乙', 2_800_000, 2_800_000),
                                 ('义丙', 2_800_000, 2_800_000)])
    # free=1.6M<floor（承诺 8.4M，成本 8.4M…义仓 0.2M）
    _make_direct_seg(env, '义仓', 200_000, 0.0, cost_qty=20_000)
    # 第一笔轮换：义甲 → 入A（放行，义务=1 未平仓）
    pools.allocate('入A', 500_000, reason='轮1', code='sh1',
                   entry_mode='rotation', rotation_out='义甲')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('入A', quantity=40_000, note='轮1 首仓')
        # 第二笔轮换：义乙 → 入B —— 义务A 未销账（义甲仍持仓）→ 拒
        pools.allocate('入B', 500_000, reason='轮2', code='sh1',
                       entry_mode='rotation', rotation_out='义乙')
        with pytest.raises(ValueError, match='义务|轮换'):
            _trader(env).buy_stock('入B', quantity=40_000, note='轮2 首仓')
        # 卖出义甲全部（28 万股@10=280 万）→ 义务A 销账（pool_return 回池 + 清仓 release）
        _trader(env).sell_stock('义甲', sell_all=True, note='轮换义务执行完毕')
        # 义务清零 → 入B 放行
        _trader(env).buy_stock('入B', quantity=40_000, note='轮2 首仓（义务清零后）')
    seg_b = _seg_row(env, '入B')
    assert seg_b is not None and seg_b['status'] == 'open'
    # 义甲清仓 release 后义务链闭合：280 万回池（pool_return）
    assert len(_audit_rows(env, 'pool_return', '义甲')) == 1


# ============ c) 判例锁（9/3 裁决重铸）：门基于真实值 ============

def test_c1_sinopec_precedent_reclassified(pools, env):
    """判例锁重铸：9/2 中国石化场景（承诺 81% 但真实占用仅 ~9%）在真实值口径下
    **不再是轮换门拦截对象**——预算睡觉不该挡新入场（假稀缺根除，用户 9/3 裁决）。
    当年真正的拦截应是 floor（free 1.0M < 2M）——由 b 组锁管。"""
    _commitment_env(env, pools, [('判例甲', 3_000_000, 300_000),
                                 ('判例乙', 3_000_000, 300_000),
                                 ('判例丙', 2_100_000, 300_000)])   # 承诺 8.1M/真实 0.9M=9%
    pools.allocate('中国石化', 1_000_000, reason='真实 9%≤2/3 → 直放（承诺 81% 不再拦）')
    assert _seg_row(env, '中国石化') is not None


# ============ d) 自动拨付幂等 + sell→pool 逐分 ============

def test_d1_auto_grant_and_sell_return_to_pool(pools, env):
    """段 cash 不足 → 自动从 pool 拨付差额（pool_grant）；sell 回款直接回 pool
    （pool_return）逐分；清仓 release 零双计（残留 cash≈0）。"""
    _make_direct_seg(env, '拨票', 500_000, 100_000, entry_mode='normal', source='agent')
    free0 = _free(env)                       # 9.9M（helper 已扣段 cash 10 万，账本诚实）
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('拨票', quantity=30_000, note='首仓 30万')   # 段 cash 10万 + 拨付 20万
    assert _free(env) == pytest.approx(free0 - 200_000), "buy 出池=成交−段现金（拨付差额）"
    seg = _seg_row(env, '拨票')
    assert (seg['cash'] or 0) == pytest.approx(0.0), "拨付即消耗"
    grants = _audit_rows(env, 'pool_grant', '拨票')
    assert len(grants) == 1 and grants[0]['amount'] == pytest.approx(200_000)
    # 涨到 11 → 全部卖出 33万 → 逐分回 pool
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(11.0)
        _trader(env).sell_stock('拨票', sell_all=True, note='清仓')
    returns = _audit_rows(env, 'pool_return', '拨票')
    assert len(returns) == 1 and returns[0]['amount'] == pytest.approx(330_000)
    # sell 触发清仓自动 release：残留 cash≈0 → release 不双计
    assert _free(env) == pytest.approx(free0 - 200_000 + 330_000), "sell 逐分回池，无泄漏"
    conn = _conn(env)
    seg2 = conn.execute("SELECT status, cash FROM position WHERE stock='拨票' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert seg2['status'] == 'closed' and abs(seg2['cash'] or 0) < 0.01


def test_d2_manual_cash_covered_buy_no_grant(pools, env):
    """段 cash 足覆 → 不动 pool.free（先段后池）。"""
    _make_direct_seg(env, '自覆票', 500_000, 500_000, entry_mode='normal', source='agent')
    free0 = _free(env)                       # 9.5M（cash 在段不在池）
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('自覆票', quantity=10_000, note='段现金自覆')
    assert _free(env) == pytest.approx(free0), "段现金覆盖 → pool 分文不动"
    assert _audit_rows(env, 'pool_grant', '自覆票') == []
    assert _seg_row(env, '自覆票')['cash'] == pytest.approx(400_000)


def test_d3_idempotent_replay_no_double_count(pools, env):
    """重跑语义：同笔拨付/回款各一笔 audit；重复 release 拒（零双计）。"""
    _make_direct_seg(env, '幂票', 400_000, 0.0, entry_mode='normal', source='agent')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('幂票', quantity=20_000, note='建仓 20万')
        mp.return_value = _PI(9.0)
        _trader(env).sell_stock('幂票', sell_all=True, note='亏损清仓 18万')
    assert len(_audit_rows(env, 'pool_grant', '幂票')) == 1
    assert len(_audit_rows(env, 'pool_return', '幂票')) == 1
    assert _free(env) == pytest.approx(10_000_000 - 200_000 + 180_000)
    with pytest.raises(ValueError):
        pools.release('幂票', reason='重复 release')      # 段已 closed，双计出局


# ============ e) manual 段全豁免 ============

def test_e1_manual_exempt_commitment_and_floor(pools, env):
    """source=manual 段：承诺率门豁免 + floor 门豁免（L1 人工特权同款）。"""
    _commitment_env(env, pools, [('人手甲', 3_000_000, 3_000_000),
                                 ('人手乙', 3_000_000, 3_000_000),
                                 ('人手丙', 2_050_000, 2_050_000)])
    # free=1.95M<floor 2M
    pools.allocate('人工票', 500_000, reason='人工裁决', source='manual',
                   code='sh1')              # 承诺>80% 但 manual 豁免 → 放行
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('人工票', quantity=50_000, note='人工首仓 50万')
    assert _free(env) == pytest.approx(1_450_000), "manual floor 豁免：只扣真实成交额"


# ============ f) 迁移 pool_publicize 幂等 + 分池恒等式 ============

def test_f1_pool_publicize_moves_cash_and_idempotent(env, pools):
    """非 NEWS open 段 cash→pool free（逐笔 audit v11_publicize + summary）；
    NEWS 段严禁触碰；重跑 rowcount=0 零变化。"""
    from paper_trading_v2 import pool_publicize
    pools.init_pool(2_000_000, pool='sleeve')          # 主池 base 8M
    # 两个技术段滞留现金（helper 诚实扣池：free=8M−40万−20万=7.4M）+ 1 NEWS 成员段（禁碰）
    _make_direct_seg(env, '迁甲', 400_000, 300_000, cost_qty=10_000)
    _make_direct_seg(env, '迁乙', 200_000, 200_000)
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(env / 'master_pool.db').open_slot(
        ['迁NEWS'], budget=400_000, event_key='ND#P12', code_map={'迁NEWS': 'sh1'})
    conn = _conn(env)
    news_cash_before = conn.execute(
        "SELECT cash FROM position WHERE stock='迁NEWS' AND status='open'").fetchone()[0]
    conn.close()

    main_free0 = _free(env)
    sleeve_free0 = _free(env, 'sleeve_ledger')
    assert main_free0 == pytest.approx(7_400_000)

    # 预演（不 execute）：零变化 + per-stock 打印
    preview = pool_publicize.publicize(env / 'master_pool.db', execute=False)
    assert preview['moved_total'] == pytest.approx(500_000)
    assert {m['stock'] for m in preview['moves']} == {'迁甲', '迁乙'}
    assert _free(env) == pytest.approx(main_free0)

    r1 = pool_publicize.publicize(env / 'master_pool.db', execute=True)
    assert r1['rowcount'] == 2 and r1['moved_total'] == pytest.approx(500_000)
    assert _free(env) == pytest.approx(main_free0 + 500_000)
    assert _free(env, 'sleeve_ledger') == pytest.approx(sleeve_free0)   # 消息池分毫不动
    conn = _conn(env)
    cashes = dict(conn.execute("SELECT stock, cash FROM position WHERE status='open' "
                               "AND COALESCE(strategy,'')!='NEWS'").fetchall())
    news_cash = conn.execute("SELECT cash FROM position WHERE stock='迁NEWS' AND "
                             "status='open'").fetchone()[0]
    conn.close()
    assert cashes == {'迁甲': 0.0, '迁乙': 0.0}
    assert news_cash == news_cash_before, "NEWS 段严禁触碰"
    per = _audit_rows(env, 'v11_publicize')
    assert {r['stock'] for r in per} == {'迁甲', '迁乙'}
    assert len(_audit_rows(env, 'v11_publicize_summary')) == 1

    # 幂等重跑：rowcount=0、库零变化
    r2 = pool_publicize.publicize(env / 'master_pool.db', execute=True)
    assert r2['rowcount'] == 0 and r2['moved_total'] == pytest.approx(0.0)
    assert _free(env) == pytest.approx(main_free0 + 500_000)
    assert len(_audit_rows(env, 'v11_publicize')) == 2

    # 公开化后段转入 v11 信封制（盖章：sell/buy 走 grant/return 路径）：sell 回池成立
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(11.0)
        _trader(env).sell_stock('迁甲', sell_all=True, note='公开化后首卖')
    assert len(_audit_rows(env, 'pool_return', '迁甲')) == 1


def test_f2_split_pool_identity(env, pools):
    """reconcile 重铸：每池 free + Σ段cash + Σ净持仓成本 − Σ已实现 == total 各自成立；
    技术段 cash>0 → WARN 哨兵。"""
    from typer.testing import CliRunner
    from paper_trading_v2.cli import app
    runner = CliRunner()
    pools.init_pool(2_000_000, pool='sleeve')          # 主池 8M
    _make_direct_seg(env, '恒甲', 1_000_000, 300_000, cost_qty=70_000)   # cash+FIFO=budget
    _make_direct_seg(env, '恒乙', 500_000, 0.0, cost_qty=50_000)         # 满仓
    # helper 已扣池：free=8M−1.5M；分池恒等式 7M? 不——7.5M−? 装配自查：free=8M−(30万+70万)−50万=6.2M
    r = runner.invoke(app, ["reconcile", "--detail"])
    assert r.exit_code == 0, r.output
    assert "主池分池恒等式" in r.output and "消息池分池恒等式" in r.output
    assert "✅ 恒等式在容差内" in r.output, r.output
    assert "WARN" in r.output and "滞留" in r.output, "技术 open 段 cash>0 哨兵未触发"
    # 制造超差（主池抽走 60 万 > 容差）→ 分池告警（旧 U7.5 语义保留）
    _set_free(env, _free(env) - 600_000)
    r2 = runner.invoke(app, ["reconcile"])
    assert "超差告警" in r2.output, r2.output


def test_f3_reconcile_shows_watermarks(env, pools):
    """show + reconcile 加承诺率/真实率/floor 水位（v11 口径）。"""
    from typer.testing import CliRunner
    from paper_trading_v2.cli import app
    _make_direct_seg(env, '水位甲', 3_000_000, 2_500_000, cost_qty=50_000)   # 成本 50万
    d = pools.show()
    assert 'commitment_rate' in d and 'real_rate' in d and 'floor' in d and 'rotation_gate' in d
    r = runner_out = CliRunner().invoke(app, ["reconcile"])
    # 9/3 口径：门口径=真实占用率（展示含 66.7% 门），承诺率降级展示口径
    assert "v11 水位" in r.output and "真实占用率" in r.output and "66.7%" in r.output
    assert "承诺率" in r.output and "展示" in r.output


# ============ g) NEWS 池零行为变化（6 pending 回归锁的账面侧） ============

def test_g1_news_pool_paths_untouched(env):
    """NEWS allocate/topup/fill 现金流与 v9 逐分一致：allocate 搬 cash、fill 耗段 cash、
    topup 进段 cash——本文件任何改动不得触碰。"""
    from paper_trading_v2.master_pool import MasterPoolManager
    from paper_trading_v2.sleeve_open import SleeveOpener
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(10_000_000)
    m.init_pool(2_000_000, pool='sleeve')
    op = SleeveOpener(env / 'master_pool.db')
    op.open_slot(['槽票'], budget=500_000, event_key='ND#G11', code_map={'槽票': 'sh1'})
    conn = _conn(env)
    seg = conn.execute("SELECT cash, budget FROM position WHERE stock='槽票' AND "
                       "status='open'").fetchone()
    conn.close()
    assert seg['cash'] == pytest.approx(500_000)          # 槽预算照旧进段 cash
    op.fill_pending(event_key='ND#G11', open_prices={'槽票': 10.0}, skip_conditions=True)
    conn = _conn(env)
    seg = conn.execute("SELECT cash, budget FROM position WHERE stock='槽票' AND "
                       "status='open'").fetchone()
    free_sleeve = conn.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    conn.close()
    assert seg['cash'] == pytest.approx(0.0)              # fill 全额消耗段 cash
    assert free_sleeve == pytest.approx(1_500_000)        # 消息池扣 50万，主池无关
    assert _free(env) == pytest.approx(8_000_000)


def test_g2_news_allocate_topup_keeps_cash_model(env):
    """master-pool allocate/topup pool='sleeve'：段 cash 模型不变（信封化只限主池）。

    双段形态（对齐 m16）：tech 段锚定寻址（纯 NEWS 票 topup 被灰度加仓锁拒是
    v9 既有闸门，非 v11 引入——本锁只验 cash 流不动）。"""
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(10_000_000)
    m.init_pool(2_000_000, pool='sleeve')
    _make_direct_seg(env, '槽成员', 100_000, 100_000)   # tech 段（寻址锚）
    m.allocate('槽成员', 300_000, reason='事件', pool='sleeve', grp='news')
    seg = _seg_row(env, '槽成员', strategy_not_news=False)
    assert seg['cash'] == pytest.approx(300_000), "NEWS 段 allocate 仍搬 cash（红线）"
    m.topup('槽成员', 100_000, reason='等权', pool='sleeve')
    seg = _seg_row(env, '槽成员', strategy_not_news=False)
    assert seg['cash'] == pytest.approx(400_000)
    assert _free(env, 'sleeve_ledger') == pytest.approx(1_600_000)
    assert _free(env) == pytest.approx(7_900_000)   # 主池只被 tech 段扣 10 万（helper 诚实装配）


# ============ h) topup：floor 豁免 + 30% 帽保留 + 信封化 ============

def test_h1_topup_floor_exempt_cap_kept(pools, env):
    """topup（qty>0 再买场景的机动注资）：floor 豁免（机动资金本意，经 buy 验证）；
    ≤30% 帽保留。"""
    _make_direct_seg(env, '帽票', 1_500_000, 0.0, cost_qty=150_000)  # 满仓 150 万，持仓中
    _set_free(env, 1_000_000)              # free 1.0M < floor 2M（深水区）
    pools.topup('帽票', 500_000, reason='机动注资')       # 信封加码：free 不动
    assert _free(env) == pytest.approx(1_000_000)
    seg = _seg_row(env, '帽票')
    assert seg['budget'] == pytest.approx(2_000_000)
    # 机动资金 buy（qty>0 再买）floor 豁免：深水区仍放行（拨付 30 万）
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('帽票', quantity=30_000, note='机动加仓')
    assert _free(env) == pytest.approx(700_000), "topup buy floor 豁免"
    with pytest.raises(ValueError, match='30%'):
        pools.topup('帽票', 1_100_000, reason='超帽')      # 2.0M+1.1M > 3M=30%×10M


def test_h2_topup_envelope_no_cash_move(pools, env):
    """topup 信封化：只加 budget（承诺），不搬段 cash（v11 机动权利非现金）。"""
    _make_direct_seg(env, '信封注', 1_000_000, 800_000, cost_qty=20_000)
    seg0 = _seg_row(env, '信封注')
    free0 = _free(env)
    pools.topup('信封注', 200_000, reason='权利加码')
    seg1 = _seg_row(env, '信封注')
    assert seg1['budget'] - seg0['budget'] == pytest.approx(200_000)
    assert (seg1['cash'] or 0) == pytest.approx(seg0['cash'] or 0), \
        "topup 信封化：段 cash 不增（机动权利≠预支现金）"
    assert _free(env) == pytest.approx(free0), "topup 信封化：free 不动"
