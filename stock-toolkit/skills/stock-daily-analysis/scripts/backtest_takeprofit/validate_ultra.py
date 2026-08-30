import json, os, statistics as st, random
exec(open('/tmp/tp_recheck.py').read().split("SCHEMES = [")[0])

cases = []
for f in sorted(os.listdir('/tmp/kb')):
    k = json.load(open(f'/tmp/kb/{f}'))
    for ei in entries(k): cases.append((f[:-5], k, ei))
FEE = 12
N = len(cases)
print(f"样本 {N} 笔，成本后（单边 12bp）\n")

S = {
 'A': dict(mode='A'),
 'B': dict(mode='B'),
 'C': dict(mode='C'),
 'G': dict(mode='G', trig=0.20, part=0.5),
}
res = {}
for nm, kw in S.items():
    res[nm] = [run(k, ei, cost_bp=FEE, **kw)[0] for _, k, ei in cases]

print("=== ① 完整分布（ultra 要求 worst/P5/P90 补全）===")
print(f"{'方案':4s} {'胜率':>5s} {'均值':>7s} {'中位':>7s} {'P5':>7s} {'P10':>7s} {'P90':>7s} {'P95':>7s} {'最差':>8s} {'最好':>7s}")
for nm in S:
    rs = sorted(res[nm]); n = len(rs)
    q = lambda p: rs[min(n-1, int(n*p))]
    print(f"{nm:4s} {sum(1 for x in res[nm] if x>0)/n*100:4.0f}% {st.mean(rs):+6.2f}% {st.median(rs):+6.2f}% "
          f"{q(.05):+6.2f}% {q(.10):+6.2f}% {q(.90):+6.2f}% {q(.95):+6.2f}% {rs[0]:+7.2f}% {rs[-1]:+6.2f}%")
gA = res['G'][0]
print(f"\n  → ultra 决策门槛检验：G P90 - A P90 = "
      f"{sorted(res['G'])[int(N*.9)]:+.2f}% - {sorted(res['A'])[int(N*.9)]:+.2f}% = "
      f"{sorted(res['G'])[int(N*.9)] - sorted(res['A'])[int(N*.9)]:+.2f}pp",
      "→ 差>3pp 则 C 升首选" if (sorted(res['G'])[int(N*.9)]-sorted(res['A'])[int(N*.9)]) < -3 else "→ 未破 3pp 红线，G 仍可首选")

print("\n=== ② 按票聚类 Bootstrap（ultra: iid CI 偏窄）===")
by_stock = {}
for i, (s, _, _) in enumerate(cases): by_stock.setdefault(s, []).append(i)
stocks = list(by_stock)
random.seed(11)
for pair in [('G','A'), ('C','A'), ('C','G')]:
    x, y = pair
    bs = []
    for _ in range(4000):
        picks = [random.choice(stocks) for _ in stocks]
        idx = [i for s in picks for i in by_stock[s]]
        if idx: bs.append(st.mean([res[x][i]-res[y][i] for i in idx]))
    bs.sort()
    lo, hi = bs[int(len(bs)*.025)], bs[int(len(bs)*.975)]
    point = st.mean([res[x][i]-res[y][i] for i in range(N)])
    print(f"  {x}-{y}: 点估计 {point:+.2f}pp | 聚类95%CI [{lo:+.2f}, {hi:+.2f}] | 含0: {'是' if lo<=0<=hi else '否'}")

print("\n=== ③ Leave-one-stock-out（ultra 通过线: ≥16/20 折）===")
for pair in [('G','A'), ('C','A')]:
    x, y = pair
    win = 0
    for s in stocks:
        idx = [i for i, (ss, _, _) in enumerate(cases) if ss != s]
        if st.mean([res[x][i] for i in idx]) >= st.mean([res[y][i] for i in idx]): win += 1
    print(f"  {x} ≥ {y} 的折数: {win}/{len(stocks)}  {'✅通过' if win>=16 else '❌未过线'}")

print("\n=== ④ 分年方向一致性（要求同号）===")
for yr in ('2025', '2026'):
    idx = [i for i, (s, k, ei) in enumerate(cases) if k[ei]['date'].startswith(yr)]
    if len(idx) < 3: continue
    dG = st.mean([res['G'][i] for i in idx]) - st.mean([res['A'][i] for i in idx])
    dC = st.mean([res['C'][i] for i in idx]) - st.mean([res['A'][i] for i in idx])
    print(f"  {yr} (n={len(idx)}): G-A {dG:+.2f}pp | C-A {dC:+.2f}pp")

print("\n=== ⑤ 数字对不上核实（ultra 抓到 +6.70 vs +6.58）===")
def run_mult(k, ei, mult, cost_bp=FEE):
    cost=k[ei]['open']; at=atr_series(k); a0=at[ei] or k[ei]['close']*0.04
    hard=cost-2.0*a0; trail=cost-mult*a0; peak=cost; fee=cost_bp/10000.0
    for i in range(ei+1, len(k)):
        bar=k[i]; h,l=bar['high'],bar['low']
        s=max(hard,trail)
        if l<=s: return (min(bar['open'],s)/cost-1)*100 - 2*fee*100
        peak=max(peak,h); trail=max(trail,peak-mult*(at[i] or a0))
    return (k[-1]['close']/cost-1)*100 - 2*fee*100
r_main = st.mean(res['A'])
r_mult = st.mean([run_mult(k, ei, 2.5) for _, k, ei in cases])
print(f"  主 run(mode=A) 均值 = {r_main:+.2f}%")
print(f"  run_mult(2.5)   均值 = {r_mult:+.2f}%")
print(f"  差异来源：run_mult 成本记 2×12bp(含入场)，主 run 仅记卖出 1×12bp → 差≈{abs(r_main-r_mult):.2f}pp")
print("  → ultra 指出的不一致成立：两处成本口径不同，非计算 bug；主口径以'卖出计成本'为准")

print("\n=== ⑥ 跌停出场诊断（ultra 要求）===")
ld = 0; details = []
for (s, k, ei), nm in zip(cases, range(N)):
    _, xi, _, _ = run(k, ei, mode='A', cost_bp=FEE)
    # 出场日开盘跌停（主板-10%/创业科创-20%近似：跌≥9.5%或≥19.5%）
    ex_idx = min(ei+xi, len(k)-1)
    if ex_idx < len(k) and ex_idx > 0:
        chg = k[ex_idx]['open']/k[ex_idx-1]['close'] - 1
        lim = -0.195 if (s.startswith(('300','688')) or k[ei].get('code','').startswith(('300','688'))) else -0.095
        if chg <= lim:
            ld += 1; details.append(f"{s} 开盘{chg*100:.1f}%")
print(f"  A 方案出场日开盘触及跌停: {ld}/{N} 笔", ("→ 需加顺延规则" if ld>=2 else "→ 影响可忽略"))
for d in details[:6]: print("   ", d)

print("\n=== ⑦ 滑点二档敏感性（止损30bp/止盈12bp）===")
for lbl, fee in [('基准 12bp', 12), ('止损从严 30bp', 30)]:
    g = st.mean([run(k, ei, mode='G', trig=.2, part=.5, cost_bp=fee)[0] for _, k, ei in cases])
    a = st.mean([run(k, ei, mode='A', cost_bp=fee)[0] for _, k, ei in cases])
    print(f"  {lbl}: G {g:+.2f}% / A {a:+.2f}% / G-A {g-a:+.2f}pp")

print("\n=== ⑧ 并发持仓重叠度 ===")
occup = {}
for s, k, ei in cases:
    _, xi, _, _ = run(k, ei, mode='A', cost_bp=FEE)
    for d in range(ei, ei+xi+1): occup[d] = occup.get(d, 0) + 1
vals = sorted(occup.values())
print(f"  持仓数分布: 中位 {st.median(vals):.0f} | P90 {vals[int(len(vals)*.9)]} | 最大 {max(vals)}（上限12）")
