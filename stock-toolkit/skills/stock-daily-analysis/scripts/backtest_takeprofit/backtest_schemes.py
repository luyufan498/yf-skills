import json, os, statistics as st, random

def atr_series(k, n=14):
    out = [None]*len(k); trs = []
    for i in range(1, len(k)):
        h, l, pc = k[i]['high'], k[i]['low'], k[i-1]['close']
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if len(trs) < n: return out
    a = sum(trs[:n])/n; out[n] = a
    for i in range(n+1, len(k)):
        a = (a*(n-1)+trs[i-1])/n; out[i] = a
    return out

def run(k, ei, mode, trig=0.20, part=0.5, lock=0.12, t1=0.30, t2=0.50, cost_bp=0):
    """cost_bp: 单边成本(基点). T+1: 入场当日不可卖.
       保守 peak: 只用前一日收盘更新(避免盘中偷未来)."""
    cost = k[ei]['open']; at = atr_series(k)
    a0 = at[ei] or k[ei]['close']*0.04
    hard = cost - 2.0*a0; trail = cost - 2.5*a0; peak = cost
    mfe = 0.0
    sold = []; rem = 1.0
    fee = cost_bp/10000.0
    for i in range(ei+1, len(k)):
        bar = k[i]; h, l, c = bar['high'], bar['low'], bar['close']
        mfe = max(mfe, h/cost-1)
        # 出场判定（用上一日收盘确定的 trail/hard）
        s = max(hard, trail)
        if mode == 'F' and peak >= cost*(1+trig):
            s = max(s, peak*(1-lock))
        # 分批止盈（T+1: i>ei+1 才允许部分止盈卖出, 保守起见入场次日才可卖）
        if mode == 'C':
            if len(sold) == 0 and h >= cost*(1+t1) and i >= ei+2:
                px = max(bar['open'], cost*(1+t1)); sold.append((px/cost-1)*0.333); rem -= 0.333
            if len(sold) == 1 and h >= cost*(1+t2) and i >= ei+2:
                px = max(bar['open'], cost*(1+t2)); sold.append((px/cost-1)*0.333); rem -= 0.333
        if mode in ('G','H') and len(sold) == 0 and h >= cost*(1+trig) and i >= ei+2:
            px = max(bar['open'], cost*(1+trig)); sold.append((px/cost-1)*part); rem -= part
        if mode == 'B' and peak >= cost*1.15:
            s = max(s, cost*1.002)
        if mode == 'H' and peak >= cost*(1+trig):
            s = max(s, peak*(1-lock))
        if mode == 'D' and h >= cost*1.40:
            px = max(bar['open'], cost*1.40)
            return (px/cost-1)*100 - 2*fee*100, i-ei, mfe*100, '目标价清'
        if mode == 'E' and h >= cost*1.25:
            px = max(bar['open'], cost*1.25)
            return (px/cost-1)*100 - 2*fee*100, i-ei, mfe*100, '固定清'
        if l <= s:
            px = min(bar['open'], s)
            gross = (px/cost-1)*rem + sum(sold)
            turns = 1 + len(sold)          # 卖出次数
            return gross*100 - turns*fee*100, i-ei, mfe*100, '止损/锁盈'
        # 收盘后才更新 peak / trail（无未来函数）
        peak = max(peak, h)
        trail = max(trail, peak - 2.5*(at[i] or a0))
    gross = (k[-1]['close']/cost-1)*rem + sum(sold)
    return gross*100 - (1+len(sold))*fee*100, len(k)-1-ei, mfe*100, '期末'

def entries(k, min_idx=30):
    e = []; last = -99
    for i in range(min_idx, len(k)-5):
        mp = k[i-1]['close']/k[i-11]['close']-1; mn = k[i]['close']/k[i-11]['close']-1
        if mp < 0.15 <= mn <= 0.25 and i-last >= 20: e.append(i); last = i
    return e

cases = []
for f in sorted(os.listdir('/tmp/kb')):
    k = json.load(open(f'/tmp/kb/{f}'))
    for ei in entries(k): cases.append((f[:-5], k, ei))

SCHEMES = [
    ('A 纯ATR跟随',      dict(mode='A')),
    ('B ATR+保本锁15%',  dict(mode='B')),
    ('C +30/50各卖1/3',  dict(mode='C')),
    ('F 回撤12%锁盈',    dict(mode='F')),
    ('G +20%卖半余ATR',  dict(mode='G')),
    ('H 卖半+回撤12%',   dict(mode='H')),
]
FEE = 12   # 单边 12bp ≈ 印花税0.05%卖出+佣金，保守

print("=== 修正版：全方案 + T+1 + 收盘更新peak + 成本12bp ===\n")
print(f"{'方案':20s} {'胜率':>5s} {'均值':>7s} {'中位':>7s} {'P10':>7s} {'P90':>7s} {'最差':>7s} {'95%CI(Bootstrap)':>20s}")
results = {}
for name, kw in SCHEMES:
    rs = [run(k, ei, cost_bp=FEE, **kw)[0] for _, k, ei in cases]
    results[name] = rs
    n = len(rs); s = sorted(rs)
    # bootstrap 均值 95% CI
    random.seed(42)
    means = sorted(st.mean(random.choices(rs, k=n)) for _ in range(2000))
    lo, hi = means[50], means[1949]
    print(f"{name:20s} {sum(1 for x in rs if x>0)/n*100:4.0f}% {st.mean(rs):+6.2f}% {st.median(rs):+6.2f}% "
          f"{s[int(n*.1)]:+6.2f}% {s[int(n*.9)]:+6.2f}% {s[0]:+6.2f}%  [{lo:+.2f}, {hi:+.2f}]")

# G vs A 配对差 bootstrap（关键：差异是否显著）
print("\n=== G-A 配对差（Bootstrap 2000次）===")
diffs = [g-a for g, a in zip(results['G +20%卖半余ATR'], results['A 纯ATR跟随'])]
random.seed(7)
dmeans = sorted(st.mean(random.choices(diffs, k=len(diffs))) for _ in range(2000))
print(f"  配对均值差: {st.mean(diffs):+.2f}pp | 95%CI [{dmeans[50]:+.2f}, {dmeans[1949]:+.2f}]")
print(f"  CI 含 0 ? {'是 → 差异不显著' if dmeans[50] <= 0 <= dmeans[1949] else '否 → 差异显著'}")
win_diff = sum(1 for x in dmeans if x > 0)/len(dmeans)
print(f"  G 优于 A 的概率: {win_diff*100:.0f}%")

# 修正口径的子集（截至出场的 MFE，非全K线）
print("\n=== 修正口径子集（MFE 截至出场点，无未来函数）===")
mfe_cache = {}
for name, kw in SCHEMES:
    mfe_cache[name] = [run(k, ei, cost_bp=FEE, **kw)[2] for _, k, ei in cases]
for thr in (10, 20):
    idx = [i for i in range(len(cases)) if mfe_cache['A 纯ATR跟随'][i] >= thr]
    line = f"  MFE≥{thr}% ({len(idx)} 笔) 落袋: "
    for name, _ in SCHEMES[:6]:
        g = [results[name][i] for i in idx]
        line += f"{name[0]}={st.mean(g):+.1f}% "
    print(line)

# 参数敏感性网格（回应"平台 vs 尖峰"批评）
print("=== G 方案参数敏感性（均值/中位，成本后）===")
hdr = "阈值\\比例"
print(f"{hdr:10s}", end='')
for part in (0.33, 0.5, 1.0):
    print(f"{'卖'+str(part):>14s}", end='')
print()
for trig in (0.15, 0.20, 0.25, 0.30):
    print(f"+{int(trig*100):>3d}%    ", end='')
    for part in (0.33, 0.5, 1.0):
        rs = [run(k, ei, mode='G', trig=trig, part=part, cost_bp=FEE)[0] for _, k, ei in cases]
        print(f"{st.mean(rs):+6.2f}/{st.median(rs):+5.2f} ", end='')
    print()
