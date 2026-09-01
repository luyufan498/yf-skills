"""M1.8 sleeve-pool-init 配对扣减回归测试（R1 划拨 / R2 守恒门）

- ① init 配对：主池 total/free 各 -X、sleeve +X、Σtotal 恒=注入基准 10M、audit 两行
- ② 主池 free 不足 → 拒绝且库零变化（配对回滚，主池扣了消息池没建不许落库）
- ③ 并发 init×2 → 仅一成一拒（已初始化拒绝分支 + 条件扣减 + PK 约束，主池只扣一次）
- ④ 回归锁：手工裸 INSERT 未配对 sleeve_ledger（旧版 init 洞/越权写）→ reconcile ⚠️
  （修前必红：旧代码 W 与 Σtotal 同步膨胀 drift 恒=0 静默；修后必绿）
- ⑤ 20% 门边界（=20% 过、>20% 拒）
隔离：pytest 临时 workspace，零触网，生产库零接触。
"""
import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch

from paper_trading_v2.master_pool import MasterPoolManager
from paper_trading_v2.db import get_connection

# 注入基准（任务书 R2/W_BASE=10,000,000，生产 audit id=1 init 行为证）；测试用字面量钉死
W_BASE = 10_000_000


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
def main_only(env):
    """只有主池（生产同款：10M 注入，audit 有 init 行为证），消息池留给各测试自己 init。"""
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(W_BASE)
    return m


def _conn(env):
    return get_connection(env / 'master_pool.db')


def _ledgers(env):
    """(main.total, main.free, sleeve.total, sleeve.free)（缺侧=0）。"""
    conn = _conn(env)
    row = lambda t: conn.execute(f"SELECT total, free FROM {t} WHERE id=1").fetchone()
    m, s = row('pool_ledger'), row('sleeve_ledger')
    conn.close()
    return (m['total'] if m else 0.0, m['free'] if m else 0.0,
            s['total'] if s else 0.0, s['free'] if s else 0.0)


def _audit_rows(env, action=None):
    conn = _conn(env)
    q = "SELECT * FROM audit"
    args = ()
    if action:
        q += " WHERE action=?"
        args = (action,)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY id", args)]
    conn.close()
    return rows


# ---------- ① init 配对划拨 ----------

def test_init_sleeve_pairs_with_main_deduction(main_only, env):
    """R1：sleeve init=从主池划拨——主池 total/free 各 -X，sleeve +X，Σtotal 恒=注入基准。"""
    m = main_only
    m.init_pool(2_000_000, pool='sleeve')
    mt, mf, st, sf = _ledgers(env)
    assert (mt, mf) == (8_000_000.0, 8_000_000.0), f"主池未配对扣减：{mt}/{mf}"
    assert (st, sf) == (2_000_000.0, 2_000_000.0), f"消息池账本异常：{st}/{sf}"
    assert mt + st == W_BASE, f"Σtotal 守恒破坏：{mt + st} ≠ {W_BASE}"
    d = m.show()
    assert d['total'] == 8_000_000 and m.show(pool='sleeve')['total'] == 2_000_000


def test_init_sleeve_audit_two_rows_transfer_plus_init(main_only, env):
    """R1：audit 两行——sleeve_init_transfer（主池 -X，free_before/after 真实）+ init（+X）。"""
    main_only.init_pool(2_000_000, pool='sleeve')
    rows = _audit_rows(env, 'sleeve_init_transfer')
    assert len(rows) == 1, f"sleeve_init_transfer 行数={len(rows)}"
    tr = rows[0]
    assert tr['amount'] == -2_000_000.0, f"主池划拨出金额={tr['amount']}"
    assert tr['free_before'] == 10_000_000.0 and tr['free_after'] == 8_000_000.0
    assert '从主池划拨' in (tr['reason'] or '')
    inits = _audit_rows(env, 'init')
    sleeve_init = [r for r in inits if r['amount'] == 2_000_000.0]
    assert len(sleeve_init) == 1, f"消息池 init 行={len(sleeve_init)}"
    assert '从主池划拨' in (sleeve_init[0]['reason'] or '')


def test_init_sleeve_reconcile_conservation_green(main_only, env):
    """R2：配对 init 后 reconcile 总量守恒 ✅（既有 W 恒等式不受影响）。"""
    from typer.testing import CliRunner
    from paper_trading_v2.cli import app
    main_only.init_pool(2_000_000, pool='sleeve')
    r = CliRunner().invoke(app, ["reconcile"])
    assert r.exit_code == 0, r.output
    assert "✅ 总量守恒" in r.output, r.output
    assert "总量守恒破坏" not in r.output
    assert "✅ 恒等式在容差内" in r.output


def test_init_sleeve_requires_main_pool(env):
    """主池未初始化时消息池无处可划——拒绝（资金必须有出处）。"""
    m = MasterPoolManager(env / 'master_pool.db')
    with pytest.raises(ValueError, match='主池未初始化'):
        m.init_pool(2_000_000, pool='sleeve')
    assert _ledgers(env) == (0.0, 0.0, 0.0, 0.0)


def test_init_main_pool_behavior_unchanged(env):
    """R1.5：--pool main（init 主池本身）行为不变——total=free=X，单条 init audit 行。"""
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(10_000_000)
    assert _ledgers(env) == (10_000_000.0, 10_000_000.0, 0.0, 0.0)
    assert len(_audit_rows(env, 'init')) == 1
    assert _audit_rows(env, 'sleeve_init_transfer') == []


# ---------- ② 主池 free 不足 → 拒绝且库零变化 ----------

def test_init_sleeve_insufficient_main_free_rolls_back_all(main_only, env):
    """R1：主池 free < X → 条件扣减 rowcount=0 → 整体拒绝，库零变化（配对回滚）。"""
    m = main_only
    # 制造"主池资金已被占用到不足 2M"（free<2M；占用本可经 allocate 达成，此处直写简化）
    conn = _conn(env)
    conn.execute("UPDATE pool_ledger SET free=1500000 WHERE id=1")
    conn.commit()
    conn.close()
    before = _ledgers(env)
    n_audit_before = len(_audit_rows(env))
    with pytest.raises(ValueError, match='主池资金不足'):
        m.init_pool(2_000_000, pool='sleeve')
    assert _ledgers(env) == before, "拒绝后库状态必须零变化"
    assert len(_audit_rows(env)) == n_audit_before, "拒绝后不得留任何 audit 行"


# ---------- ③ 并发 init×2 → 仅一成一拒 ----------

def test_concurrent_sleeve_init_single_winner(main_only, env):
    """并发 init×2：恰一成一拒；主池只扣一次；sleeve_ledger 恰 1 行；Σtotal 守恒。"""
    results = {}
    barrier = threading.Barrier(2)

    def worker(tag):
        m = MasterPoolManager(env / 'master_pool.db')
        barrier.wait(timeout=10)
        try:
            results[tag] = m.init_pool(2_000_000, pool='sleeve')
        except Exception as e:
            results[tag] = f'{type(e).__name__}: {e}'

    ts = [threading.Thread(target=worker, args=(t,)) for t in ('T1', 'T2')]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    ok = [v for v in results.values() if v is True]
    assert len(ok) == 1, f"成功数={len(ok)}（恰 1）：{results}"
    mt, mf, st, sf = _ledgers(env)
    assert (mt, mf) == (8_000_000.0, 8_000_000.0), f"主池须恰扣一次：{mt}/{mf}"
    conn = _conn(env)
    n_sleeve = conn.execute("SELECT COUNT(*) FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert n_sleeve == 1, f"sleeve_ledger 行数={n_sleeve}"
    assert mt + st == W_BASE, f"Σtotal 守恒破坏：{mt + st}"
    assert len(_audit_rows(env, 'sleeve_init_transfer')) == 1


# ---------- ④ 回归锁：裸 INSERT 未配对 sleeve_ledger → reconcile ⚠️ ----------

def test_bare_insert_unpaired_sleeve_triggers_conservation_alarm(main_only, env):
    """M1.8④ 回归锁：未配对的消息池直写（旧版 init 洞/越权写）→ reconcile ⚠️ 守恒告警。

    旧代码：W=free+Σbudget 与 Σtotal 同步膨胀 → drift 恒=0 → 静默漏检（修前必红）。
    修后：总量守恒门抓到 Σtotal ≠ 注入基准（修后必绿）。
    """
    from typer.testing import CliRunner
    from paper_trading_v2.cli import app
    # 模拟旧版 init / 越权写：sleeve_ledger 凭空 +2M，主池分文未动
    conn = _conn(env)
    conn.execute("INSERT INTO sleeve_ledger (id, total, free, updated_at) "
                 "VALUES (1, 2000000, 2000000, 't')")
    conn.commit()
    conn.close()
    mt, mf, st, sf = _ledgers(env)
    assert (mt, mf) == (10_000_000.0, 10_000_000.0) and st == 2_000_000.0  # 未配对状态就位
    r = CliRunner().invoke(app, ["reconcile"])
    assert r.exit_code == 0, r.output
    assert "超差告警" in r.output, f"守恒告警未触发：\n{r.output}"
    assert "总量守恒" in r.output, f"须为守恒门抓到（非 drift 门）：\n{r.output}"


# ---------- ⑤ 20% 门边界 ----------

def test_init_sleeve_20pct_boundary_exact_pass(main_only):
    """=20%：2,000,000 = 20%×10,000,000 → 过（docstring 承诺落地）。"""
    main_only.init_pool(2_000_000, pool='sleeve')
    assert main_only.show(pool='sleeve')['total'] == 2_000_000


def test_init_sleeve_20pct_boundary_over_rejected(env):
    """>20%：2,000,001 > 20%×10,000,000 → 拒绝，库零变化。"""
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(W_BASE)
    with pytest.raises(ValueError, match='20%'):
        m.init_pool(2_000_001, pool='sleeve')
    assert _ledgers(env) == (10_000_000.0, 10_000_000.0, 0.0, 0.0)


def test_init_sleeve_double_init_rejected(main_only, env):
    """现有"已初始化拒绝"分支保留：第二次 sleeve init 拒绝（划拨不重复发生）。"""
    main_only.init_pool(2_000_000, pool='sleeve')
    with pytest.raises(ValueError, match='已初始化'):
        main_only.init_pool(1_000_000, pool='sleeve')
    # 主池不得被第二次 init 再扣
    assert _ledgers(env) == (8_000_000.0, 8_000_000.0, 2_000_000.0, 2_000_000.0)
    assert len(_audit_rows(env, 'sleeve_init_transfer')) == 1
