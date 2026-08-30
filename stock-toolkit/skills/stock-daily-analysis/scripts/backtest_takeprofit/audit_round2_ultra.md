Let me carefully analyze this case as an arbitration expert (second-level review).

Setup: A-share simulated trading, profit-taking strategy backtest. Original proposal, first-round audit, and corrected rerun.

Materials:
① Original proposal: 12 positions, 10M pool, 4-channel entry (momentum/news/value-reversal/sticky flag), prefers 10-day momentum sweet zone [15,25]. Current exit = cost protection (cost - 2ATR) + trailing stop (peak - 2.5ATR, ratchet up). Audit found profit-taking trigger rate ≈ 0 (only 1 of 12 has take-profit attached), last 30 days 100% of sells are stops.

Backtest: 20 watchlist stocks × 250 trading days (2025-08~2026-08), entry proxy = 10-day momentum breakout into [15,25], 59 trades.
Results:
- A pure ATR trailing: win rate 59%, mean +5.10%, median +2.26%
- D fixed target 40%: 61%/+6.56%
- E fixed 25% no stop: 76%/+14.72%/worst -68.6%
- G (+20% sell half, rest ATR): 63%/+6.09%/median +4.75%/P90 +18.95%

Original proposal: 3-layer take profit = ① profit ≥15% move cost protection to cost price ② profit ≥20% sell 1/2 ③ remaining 2.5 ATR trailing.

② First audit (first-round audit): direction pass, parameters veto (only gray release). Main criticisms:
1. n=59 too small, win rate SE ±12pp, 59% vs 63% ≈ noise; thematic stocks correlated, effective sample maybe only 20-30
2. Pool = watchlist (survivorship + thematic + cognitive triple bias); single-year window (2026 accounts for 45/59 trades)
3. Entry proxy ≠ real 4-channel system, different channels should have different exit logic
4. No portfolio-level simulation (capital constraints/correlation/position limits)
5. ATR/peak look-ahead risk (intraday high updates peak then same-day low triggers = peeking at sequence)
6. Exit price min(open,stop) doesn't consider limit-down can't fill/T+1/slippage
7. No costs deducted: G-A only 1pp difference, one extra sell's cost eats it
8. MFE subset analysis: label criteria wrong (46 trades labeled ≥20% actually 25) and MFE is future info can't be used to select parameters
9. C plan defined but not reported = incomplete report
10. Recommendations: full market pool/walk-forward/parameter sensitivity grid/Bootstrap CI/cost-adjusted report/report all plans

③ Corrected rerun: added 12bp one-way costs, T+1 (partial sell from next day after entry), peak updated after close (eliminates look-ahead), MFE as of exit point (eliminates future info), added C plan, Bootstrap 2000 CI, parameter sensitivity grid.

Corrected results (59 trades, after costs):
- A pure ATR: 58% +6.70% median +2.06% P90 +27.9% CI[+3.1,+10.7]
- B + breakeven lock 15%: 59% +6.73% median +2.06% (nearly same as A, saved 4 trades from gain-to-loss in sample)
- C +30/+50 each sell 1/3: 59% mean +7.31% median +3.10% P90 +29.1% (highest mean/P90)
- G +20% sell half rest ATR: 61% +6.89% median +5.67% (highest median)
- H sell half + drawdown 12%: 61% +6.67% +5.67%
- G-A paired difference: +0.20pp, 95% CI [-0.94, +1.29] includes 0 → mean difference not significant; P(G>A) only 63%
- Parameter sensitivity: G in threshold 15/20/25/30% × ratio 0.33/0.5/1.0 grid mean stable +6.5~+7.9 → "plateau not peak"
- Pure ATR tightening test: ATR2.0 mean +5.09%/P90 +19.4% (vs 2.5's +6.58%/+27.8%) → tightening kills fat tail, G not equivalently replaceable
- Corrected MFE subset (as of exit): MFE≥20% 25 trades: A pockets +19.0% vs C +20.5% vs G +19.5%

Proposer's final recommendation: breakeven lock 15% directly live (zero-cost insurance); batch take-profit choose between G (median better/simple) and C (mean right-tail better/fatter tail protection) for gray release; 3 months later review with real notes with actual order flow.

Now the arbitration questions:

**Q1: Which of the 10 criticisms hold, which are excessive? Did the first auditor make errors? (Note: auditor asserted "46 trades should be 25" — check whether material ③ is aligned)**

Let me check the MFE issue. The auditor said: "MFE subset analysis: label criteria wrong (46 trades labeled ≥20% actually 25)". Material ③ corrected MFE subset (as of exit): MFE≥20% 25 trades. So after correction (MFE as of exit point instead of full-path MFE), the count is 25, which matches the auditor's claim of 25. Wait — actually there's subtlety. The original had 46 trades labeled ≥20% (probably because they used full-path MFE including post-exit moves? No wait — MFE is maximum favorable excursion within the trade, from entry to exit. If the original labeled 46 as ≥20%, and the corrected "MFE as of exit point" gives 25... hmm, that's odd. MFE should be computed up to exit by definition. If the original used something else — maybe they used max favorable excursion over the entire holding including... Actually "MFE as of exit point" vs what? Perhaps the original counted MFE over the full future path after entry (including after exit), which would inflate the count to 46. The corrected version caps MFE at exit, giving 25. The auditor said the correct count is 25. So material ③ is aligned with the auditor's number. 

But wait — the auditor said "46 trades labeled ≥20% actually 25". So the auditor's claim was that only 25 trades actually had MFE≥20% (within trade, up to exit). The corrected version reports 25 trades with MFE≥20%. So yes, aligned. Good — so on this specific point, the auditor was right and the correction confirms it.

However — the auditor's second part of point 8: "MFE is future info can't be used to select parameters". Is this right? Partially. Using MFE to select parameters ex-ante is future information if the selection rule at time t uses MFE which is only known at exit. But as a diagnostic (conditional analysis: "among trades that reached +20% MFE, how did different exit rules capture it"), MFE-as-of-exit is fine — it's a realized outcome at exit time, not future info relative to exit. The problem is using it to *choose* parameters for future trades — that's fine actually too, as long as you don't condition future decisions on future info. Actually the deeper issue: MFE-conditioned analysis tells you how the rule captures big winners, but you can't use it in live trading because you don't know ex-ante which trades will reach 20% MFE. But as a *diagnostic comparison* of exit rules it's legitimate — all exit rules are evaluated on the same realized paths. Hmm, actually the MFE subset analysis is legitimate as a conditional performance comparison: it answers "when a trade runs big, which rule captures more?" That's a fair diagnostic. The auditor's criticism "MFE is future info can't be used to select parameters" is partially over-strict: MFE-as-of-exit is not future info relative to the trade's end; using it for *diagnosis* is fine; using it for *live gating* (e.g., "only apply rule G to trades predicted to have MFE≥20%") would be data snooping. So the corrected version's usage (diagnostic) is acceptable. But I should note: conditioning on MFE≥20% then comparing A/C/G capture is fine because all rules face the same path; the subset is defined by a quantity known at exit; no look-ahead relative to the evaluation endpoint. However, there is a subtle selection issue: the subset is small (25 trades) and the differences (19.0 vs 20.5 vs 19.5) are ~1-1.5pp on 25 trades — SE per trade is huge (maybe 25-30pp), paired diff SE maybe 3-5pp → not significant. So the MFE subset shouldn't drive the G vs C choice.

Now go through the 10 criticisms:

1. n=59 too small, win-rate SE ±12pp: Let me verify. SE of proportion = sqrt(p(1-p)/n) = sqrt(0.59*0.41/59) = sqrt(0.2419/59) = sqrt(0.0041) = 0.064 → ±6.4pp, not ±12pp. Hmm, the auditor said ±12pp. That's wrong by ~2x, unless they used some other formula (e.g., 95% CI half-width ≈ 1.96*0.064 = 12.5pp — ah, that's it! The auditor said "win rate SE ±12pp" — actually ±12.5pp is the 95% CI half-width, not SE. So the auditor mislabeled CI as SE, but the substance (59% vs 63% ≈ noise) is right: difference 4pp vs SE of difference ~ sqrt(2)*6.4 ≈ 9pp... for paired comparison the SE of difference could be smaller due to correlation, but with binary outcomes the McNemar-type SE depends on discordant pairs. 4pp on 59 trades = ~2.4 trades difference. Roughly, discordant pairs would be small; not significant. So substance holds, label sloppy (SE vs 95% CI). Also "effective sample 20-30 due to correlation" — plausible heuristic (design effect), can't verify precisely but reasonable. So criticism 1: substance holds (small sample, differences not significant), but the "±12pp" figure is mislabeled (it's a 95% CI half-width; SE is ~±6.4pp). The corrected rerun's paired Bootstrap CI for G-A ([-0.94,+1.29]) is the right way; it confirms non-significance. So criticism 1 stands in substance.

Wait, also check: corrected results mean +6.70% for A vs original +5.10%? Costs 12bp one-way should *reduce* returns, but A mean went from +5.10% to +6.70%? That's odd. Also peak update changed from intraday to close-based — that could change results, plausibly improving trailing stop performance (close-based peak is lower than intraday peak, so stop is lower → fewer stop-outs → higher returns). Actually yes: if peak is updated intraday (using intraday high), the trailing stop = intraday peak − 2.5ATR is higher, more likely triggered; with close-based peak, the peak is lower → stop lower → trades run longer → higher means. So the change from intraday to close peak could explain +1.6pp mean improvement for A, offsetting the 24bp round-trip cost. That's plausible and actually a red flag worth noting: the "correction" changed results materially, meaning the original look-ahead was materially biasing results downward (conservative direction, interestingly). Also possible the cost is 12bp each way, round-trip 24bp — small relative to +5.1%. So the +1.6pp improvement must come from the peak fix. This is worth flagging: the corrections changed the ranking? Let's see: original ranking by mean: E 14.72 > D 6.56 > G 6.09 > A 5.10. Corrected: C 7.31 > G 6.89 > B 6.73 > A 6.70 > H 6.67. D and E dropped out of reporting? E (fixed 25% no stop, worst -68.6%) — the corrected report doesn't mention D/E. Hmm, the corrected report lists A, B, C, G, H. D (fixed 40% target) and E (fixed 25% no-stop) are missing. The auditor's criticism 9 was about C being defined but not reported; the corrected version added C but apparently dropped D and E from the report. That's a new completeness issue — though D/E were likely rejected candidates anyway (E has -68.6% worst case, clearly rejectable), the principle "report all defined plans" should apply. Minor issue.

Also: P90 for A changed from... original didn't report P90 for A. G P90 original +18.95%, corrected C P90 +29.1%, and "ATR2.5 +27.8%" in the ATR tightening test vs A's P90 +27.9%. Fine.

2. Watchlist pool bias, single-year window: Holds. 2026 accounts for 45/59 trades (76%) — wait, window is 2025-08~2026-08, so 2026 spans Jan–Aug 2026 ≈ 8 months of 13 ≈ 62% of window but 76% of trades → clustering in 2026. This is a regime concentration issue. The correction did NOT address this — no walk-forward, no regime split. The proposer's mitigation is "3-month live gray review", which is a reasonable practical response for a personal sim account. But the criticism stands and was only partially addressed. However — the auditor's demand "full market pool / walk-forward" may be excessive for a personal sim with 20 watchlist stocks and no ability to expand. A middle ground: at minimum split the 59 trades by year (2025 n=14, 2026 n=45) and report both; check whether the G-A paired diff is consistent in both sub-periods. That's doable without new data.

3. Entry proxy ≠ real 4-channel: Holds, and unfixable without live data. The honest answer: the backtest tests "exit module on momentum-flavored entries", not the full system. Mitigation: gray release with per-channel tagging so the 3-month review can split by channel. The criticism stands but its resolution is inherently live-testing.

4. No portfolio level: Holds but partially moot: with 12 positions in 1000万, position sizing constraints and correlated thematic names mean per-trade means don't aggregate linearly. For take-profit rules specifically, portfolio interaction is second-order (exits are per-position), but capital re-deployment timing matters. Practical mitigation: gray release monitors at portfolio level. Reasonable to demand a simple check: how many of the 59 trades overlap in time / concurrent open positions distribution — that's computable from existing data without new data. So criticism holds, with a cheap partial fix available that wasn't done.

5. ATR/peak look-ahead: Holds — and the correction confirmed it mattered (results moved ~+1.6pp). Actually this is where the auditor was substantively vindicated: fixing the peak update changed A's mean from +5.10 to +6.70 (or the cost + fix net effect). Note direction: the original bug made results *worse*, i.e., the original proposal's baseline was understated. The fix is right: peak updated on close, trigger checked intraday next day. But wait — there's still a subtle residual issue: even with close-based peak, the trigger check "low <= stop" during day t uses stop computed from data through t-1 close — that's clean. But exit price = min(open, stop) — if open gaps below stop, exit at open: fine. If intraday low touches stop, exit at stop level — but in reality with a limit-down you can't fill (criticism 6). Did they address limit-down fills? The corrected materials mention T+1 and costs and close-peak, but NOT limit-down non-fillability. So criticism 6 is only half-addressed (T+1 yes, slippage approximated by 12bp? limit-down no). For momentum stocks hitting +20-30% runs, limit-down risk on the way down is real in A-shares (20% limit for ChiNext/STAR? The pool includes thematic names — if 主板 10% limit, a stock that fell 10%+ from peak could gap to limit-down and be unfillable). Actually with a 2.5ATR trailing stop, the stop triggers intraday; if price is at limit-down with a queue, simulated fill at stop price overstates fillability. Quantify: how often did the simulated exit happen on days where the stock closed at limit-down? Checkable. Not done. So criticism 6: partially addressed; residual risk remains, quantify via "exit-day limit-down frequency" diagnostic — cheap to compute, not done.

6. (same as above — wait, the list is: 5 is look-ahead, 6 is execution) Let me re-number: criticism 5 = look-ahead; criticism 6 = limit-down/T+1/slippage. OK so T+1 was addressed; limit-down fill not; slippage approximated by flat 12bp (probably commission+stamp; actual stamp duty 5bp sell-side? In 2023-2025 stamp duty is 0.05% on sells = 5bp, commission ~2.5bp, so 12bp one-way is conservative-ish but flat slippage for momentum exit at stop could be higher; a stop-loss exit in a falling stock has negative selection — fills worse than mid. 12bp may understate slippage on stop exits. But it's a sim account; fine with sensitivity note).

7. No costs: valid; addressed with 12bp one-way. Post-cost G-A paired diff +0.20pp — so the cost concern was correct in spirit: the gross edge was ~1pp, costs eat much of it, and post-cost the difference is +0.20pp, insignificant. Vindicated.

8. MFE: label count corrected to 25 — aligned with auditor's claim. The "future info" criticism: as discussed, half-right. MFE-as-of-exit used as diagnostic subset is legitimate; using it to pick parameters for future live application is only a problem if you condition future decisions on it. The corrected analysis uses it as diagnostic → acceptable. But small n=25, diffs ~1.5pp, no CI given → weak evidence either way. So criticism 8: the count claim was correct and is now confirmed; the methodological ban was over-broad in principle but prudent in effect.

Hmm wait, actually let me reconsider: was the auditor's number (25) itself verifiable from the materials? Original claimed 46 trades with MFE≥20% out of 59. If the original computed MFE over the full path *including after exit* (or over the whole 250-day series after entry), 46/59 = 78% reaching +20% at some point is plausible for momentum entries. Corrected at-exit MFE: 25/59 = 42% — consistent with win rates around 59-61% and P90 ~+28%. So yes, the corrected number being 25 exactly matches the auditor's assertion. Aligned. Good — the question explicitly asks to verify this: material ③ says "MFE≥20%的25笔" — matches. So the auditor's numeric claim is confirmed by the correction.

9. C plan missing: addressed (C now reported, and it's actually the best mean/P90). Fine. But new issue: D and E now dropped from the corrected report (E was reported originally with worst -68.6%; E is important as the "no-stop" counterexample and for tail-risk comparison). Minor completeness regression.

10. Recommendations: full-market/walk-forward are institution-grade demands — excessive for a 20-stock personal sim; but Bootstrap CI, sensitivity grid, cost-adjusted reporting were done and were the right asks. So criticism 10: partially excessive (feasibility), partially implemented.

Auditor's own errors:
- (a) "SE ±12pp" mislabel: SE ≈ 6.4pp; 12pp is the 95% CI half-width. Substance survives.
- (b) "MFE is future info, cannot be used to select parameters" — over-broad: MFE-as-of-exit is legitimate for ex-post conditional comparison (all rules evaluated on same realized paths); what's forbidden is using MFE as a live-time conditioning variable. The corrected usage (diagnostic) is fine.
- (c) The auditor demanded "full-market pool + walk-forward" — infeasible/overkill for the context; the corrected version's paired Bootstrap + sensitivity grid is the proportionate response.
- (d) Possibly the auditor's "G-A only 1pp, one extra sell's cost eats it" — actually with 12bp one-way, selling half position costs 6bp of portfolio per trade on that half... G's extra sell vs A: you sell half at +20%, cost = 0.5 × 12bp = 6bp of the position ≈ 0.06pp of per-trade return if position = 1 unit. So "one extra sell's cost eats a 1pp difference" is quantitatively wrong — one extra half-sell costs ~0.06% of trade notional (12bp on half = 6bp), far less than 1pp. Wait: the trade return is measured on the position; selling half at +20% incurs 12bp on the half = 6bp of position value. The G-A mean gap was ~1pp (6.09 vs 5.10 gross). 6bp << 100bp. So the auditor's claim 7 ("一次额外卖出的成本就能吃掉" the 1pp difference) is quantitatively wrong: one extra sell costs ~0.06pp, not 1pp. Unless "成本" includes impact of selling half early (opportunity cost) — but as stated, the cost-eats-the-edge claim is off by an order of magnitude. Hmm, but wait — maybe they meant round-trip costs on the whole trade already included in both A and G... The marginal difference between G and A is one extra partial sell: cost = 12bp × 0.5 = 6bp = 0.06pp. So yes, criticism 7's arithmetic was wrong, though its conclusion (check post-cost) was right, and post-cost the difference is +0.20pp — interestingly still positive for G. Actually the corrected G-A = +0.20pp post-cost, consistent with gross gap ~1pp minus ~0.5-0.8pp from... hmm, gross gap was 6.09-5.10 = 0.99pp; corrected 6.89-6.70 = 0.19pp. Where did ~0.8pp go? The peak-fix changed both A and G; plus costs. Fine — the numbers moved, so the original 1pp gross gap wasn't stable. The auditor's instinct to demand cost-adjusted comparison was right; the specific "one sell's cost eats it" arithmetic was wrong.

Also the auditor's point 1: "59% vs 63% ≈ noise" — with paired data, McNemar: how many trades differ in win/loss between A and G? Unknown, but plausible the discordant count is ~10-15; a 2.4-trade net difference is not significant. Fine.

Also check: the auditor said "2026占45/59笔" — material confirms. OK.

Another check: corrected B (breakeven lock at +15%) "saved 4 trades from gain-to-loss" yet mean only +0.03pp vs A (6.73 vs 6.70) and win rate +1pp. If 4 trades went from loss to breakeven (~0% instead of, say, -8%?), mean should rise meaningfully: 4 trades × (0 - (-8%))/59 ≈ +0.54pp. Reported +0.03pp suggests the saved trades were near-small-loss, or offset by other trades where the lock exited early and missed further gains. Actually the lock at +15% moves stop to cost; trades that reached +15% then reversed to cost exit at ~0% (minus costs) instead of riding the ATR trail — those might have recovered above cost and exited positive under A. So B both saves some and caps others; net ≈ 0. The claim "零成本保险" (zero-cost insurance) is thus misleading: it's not zero-cost; it's roughly zero-mean but it changes the distribution (cuts left tail among winners-reversed, caps recovery). Actually "zero-cost" in the sense mean-unchanged. But the proposer recommends adopting it "directly" based on +0.03pp — that's also a non-significant difference; the honest framing: B ≈ A in mean, slightly better median? No — median identical (2.06). Win rate 59 vs 58. So B's benefit is only the 4 saved trades narrative; no measurable distributional improvement shown (no tail metrics reported for B: worst trade? P10?). Without worst-case/ES metrics for B, "zero-cost insurance" is asserted, not demonstrated. Need B's worst trade / P5 / ES to justify. That's a gap in the corrected version: B lacks tail metrics.

Also note: the breakeven lock in the original proposal was "浮盈≥15% → 成本保护上移至成本价" — but there's already "成本保护 = 成本 − 2ATR" from entry. So B replaces "cost − 2ATR" with "cost" once +15% reached. OK.

Now Q2: holes in the corrected statistical treatment; reconcile "G-A not significant → parameters untrustworthy" vs "grid is plateau → robust".

Holes:
1. Paired Bootstrap on n=59 with cross-sectional correlation (trades overlap in time, same-sector themes) — the bootstrap resamples trades i.i.d., ignoring dependence → CI too narrow. Block bootstrap by time or by stock cluster would widen CI. With effective n ~20-30, the CI [-0.94,+1.29] is likely optimistic; true uncertainty wider. Doesn't change the conclusion (already includes 0), but the precision claim is overstated.
2. The grid "stability +6.5~+7.9" — the grid ranges over threshold and ratio but the reported metric is mean over the same 59 trades; the 12 grid points share the same 59 paths → the "plateau" is highly correlated across cells; stability of means across correlated configurations is weak evidence of robustness. Also range +6.5~+7.9 is a 1.4pp spread — given per-config SE ~1.9pp (mean SE: std of per-trade returns maybe ~15%, /√59 ≈ 2pp; paired diffs smaller), the spread is within noise → "plateau" may be indistinguishable from "flat because everything is noise". The correct statement: the data cannot distinguish any of these configs from each other — which is simultaneously "robust to parameter choice" AND "no evidence any choice is better". These two statements are reconcilable: non-significance + plateau = the data are uninformative about G's exact parameters within the tested range, not evidence that G is superior. So the reconciliation: the plateau is a *necessary but weak* condition (no fragility signal), the paired CI is the *direct* test (no detectable improvement over A). Both point to: choose G/C based on *non-statistical* criteria (behavioral fit, simplicity, portfolio mechanics, tail preference), not on the point estimates.
3. Multiple comparisons: 5-6 plans × grid of 12 → selection bias; the max (C's +7.31) is inflated. Should apply e.g. white's reality check style thinking, but honestly with n=59, everything is inside noise.
4. Bootstrap CI for A is [+3.1,+10.7] — that's a CI on mean per-trade return; fine. But paired CI for G-A computed with what method — percentile bootstrap on paired diffs; with skewed returns and n=59, percentile bootstrap under-covers; BCa would be better. Minor.
5. Peak-to-close fix introduces a different mild bias: stops computed from t-1 close are executed at t intraday; realistic. But the exit price model min(open, stop) still assumes stop-level fill on touch — adverse selection on gap/limit days not modeled (already noted). Also ATR itself — which ATR? 14-day ATR presumably; computed with close-based data, fine.
6. Costs: 12bp one-way flat; stop exits in falling markets slip more; sell-half events are at +20% (less adverse) — so flat cost is OK-ish for take-profit but understates stop-exit slippage; a 2-tier cost (30-50bp on stop exits) sensitivity would be cheap. Not done.
7. T+1: applied to partial sells (next day after entry). But another T+1 subtlety: after selling half at day t, remaining half's trailing stop exit same-day is fine (selling remaining allowed). Also can you sell half twice? If +20% triggers once, ok. What if gap open above +20%: sell at open — modeled? Unclear. Minor.
8. The "ATR2.0 kills fat tail" test: comparing ATR2.0-A vs ATR2.5(+G?) — the numbers given: ATR2.0 +5.09/P90 19.4 vs "2.5的+6.58/+27.8" — note 6.58/27.8 doesn't match A's 6.70/27.9 exactly — maybe that's G at 2.5? G is 6.89/... P90 not reported for G corrected. Slight inconsistency in numbers (6.58 vs 6.70 vs 6.89) — sloppy but not fatal. The inference "G not replaceable by tighter ATR" is directionally fine: trailing tightness substitutes for partial profit-taking only in mean, not tail. OK.
9. Missing: B's tail metrics; D/E dropped; per-year split; per-stock dispersion; limit-day diagnostics; no out-of-sample holdout (e.g., tune on 2025, test 2026 — with n so small, maybe not worth it, but a split sanity check is cheap).
10. The P(G>A)=63% — that's the Bayesian-flavored bootstrap probability; fine to report, but with correlation between trades it's overstated confidence too.

Reconciliation of the two evidences (the key conceptual question):
- "G−A CI includes 0" says: we cannot detect a mean improvement of G over A at n=59; the point estimate +0.20pp is tiny relative to noise.
- "Grid plateau +6.5~7.9" says: within the tested parameter neighborhood, outcomes don't collapse; there's no cliff.
- These are consistent: the grid shows the *surface is locally flat*; the paired CI says *we can't read the surface's fine structure anyway*. Flatness + measurement noise floor → parameter choice within the plateau is a decision under indifference. The right conclusion is NOT "G is robustly good" but "any of G/C (and even A) in this neighborhood is statistically indistinguishable; choose by secondary criteria (median, tail, implementation simplicity, behavioral adherence)". Also note: the plateau claim is about mean; G's selling point is median (+5.67 vs +2.06) — the median difference G vs A (+3.6pp on median) is a distributional (location-for-dispersion) trade: G trades right-tail (P90 27.9→ lower for G? not reported! P90 for G not given in corrected report — C has 29.1, A has 27.9; G's P90 missing). Hmm — actually original G P90 18.95 vs A's? not reported originally. In corrected, G's P90 is missing. That's a reporting gap: G's claimed advantage is median; its cost should show in P90/P95. Not reported → can't verify the trade-off. Flag it.

So the reconciliation: plateau = absence of fragility evidence; non-significance = absence of superiority evidence. Both are "absence of evidence" statements; neither licenses "G works". The decision then rests on risk-preference and operational grounds, with gray-release live validation as the true test. Also warn: with n=59 and correlated trades, the honest effective sample is ~20-30; the expected detectable difference at 80% power for paired diffs is maybe ~1.5-2pp — so this backtest could never have certified a +0.2-0.7pp edge anyway. The right frame: the backtest is a *screening/falsification* tool (reject E-type no-stop, reject tightening), not a *selection* tool between close variants.

Q3: Arbitrate the three layers:

① Breakeven lock at +15% (B): Verdict — conditional adopt / modified. Rationale: post-cost mean +0.03pp vs A (neutral), win rate +1pp, saves 4 reversal trades. It's a behavioral insurance, not a return enhancer. But "zero-cost" is unproven without tail metrics; and there's a real cost channel: locking at cost after +15% converts some would-be winners into scratches (shown by mean not rising despite 4 saves). Parameter-level: +15% threshold is arbitrary; sensitivity across 10/15/20% should be checked (not reported!). At minimum: adopt at 15% only if worst-case/ES improves; alternatively move stop to cost +0.5ATR or cost−0.5ATR instead of exact cost to reduce whipsaw at the lock line; or use time-decay lock. Hmm, but be careful: recommending stop at breakeven exactly is known to cause many "saved at zero" exits that then resume up — the data here shows net zero effect. My ruling: adopt as behavioral insurance with (a) require reporting of P5/worst for B, (b) threshold from the grid: if the 15% lock wasn't in the sensitivity grid (grid was for G's threshold), do a quick 10/15/20 lock threshold check; (c) implementation: lock triggers once high-water profit ≥15% (close-based high-water to avoid intraday noise), stop = max(cost, existing cost−2ATR) — note lock can only raise the stop, keep ratchet.

Actually, let me think about whether to recommend adopting B at all. The proposer wants B live immediately ("零成本保险"). My arbitration: conditional adopt — it's justified not by backtest edge (none, +0.03pp) but by asymmetry of purpose (cuts one tail of the win-reversal distribution) and behavioral value; but must verify with tail metrics and confirm it doesn't cut P90 materially (B's P90 not reported!). If B's P90 ≈ A's P90, adopt; if B's P90 materially lower, then the insurance has a visible premium. Given median identical and mean identical, likely P90 similar. Require the missing numbers before final sign-off. So: conditional pass on layer ①.

② G vs C: This is the real decision. Evidence: C mean +7.31 / median +3.10 / P90 +29.1; G mean +6.89, median +5.67, P90 missing. G-A paired +0.20pp CI includes 0. C vs A? Not reported as paired CI! Only G-A was paired-tested. So C's apparent superiority (+0.61pp over G, +0.42pp over A... wait 7.31-6.70=0.61 over A) has no CI. Flag: the proposer asks to choose between G and C but only quantified uncertainty for G-A. C involves TWO partial sells (at +30 and +50) → more executions → more cost and more tracking burden; at 12bp one-way, C's extra costs ≈ 0.5-position... C sells 1/3 at +30 and 1/3 of remaining? "各卖1/3" — sell 1/3 at +30%, 1/3 at +50%, keep 1/3 with ATR. Extra sells vs A: two sells of ~1/3 and ~1/2 of remainder → cost impact small (~0.08pp). Fine.

Decision logic: G (sell half at +20%) has better median → more trades get a decent lock → psychologically stickier, simpler (one trigger). C keeps more runner exposure (only 2/3 sold across two triggers, retains 1/3) → better mean/P90 → more convexity. With MFE≥20% subset: C 20.5 > G 19.5 > A 19.0 — C captures big runners best, consistent with convexity logic. Given the account's complaint was "止盈挂载率≈0，卖出100%是止损" — the behavioral goal is to make sure profits get banked → favors G (higher median, earlier first sale at +20 vs +30). But C's +30 first trigger means fewer early banks... Actually C's first sell at +30%, G's at +20%. For behavior modification (from all-stop-outs to banking profits), earlier trigger = more frequent positive reinforcement → G. And G is simpler (one threshold, one action) → higher implementation fidelity. My ruling: gray-release G first (median + simplicity + first-trigger at +20 aligns with the diagnosed behavioral problem), with a pre-registered switch rule: after 3 months/≥15 closed trades with the partial-sell layer active, if median realized capture on trades reaching +15% MFE is below A's historical capture and P90 shows no gain, flip to C or drop the layer. Alternatively split: use C for momentum-channel entries (right-tail trades), G for others — no, too complex for a personal sim; keep one rule. Parameters: G = threshold +20%, sell 50%, remainder 2.5ATR close-based trail; add a floor: first-sell only if T+1 eligible; don't re-trigger (one partial per trade). If choosing C: thresholds +30/+50 sell 1/3 each — but note C's +50 trigger hit how often? With P90 29.1, trades reaching +50 are few (maybe 5-8 of 59) → the second sell rarely fires; C ≈ "sell 1/3 at +30" + trail for most trades. That makes C closer to a "1/3 at +30" rule. Fine to note.

Actually wait — I should double check the G vs C choice against the paired evidence: neither C-A nor C-G paired CIs reported. With G-A CI [-0.94,+1.29], C-G point diff +0.42pp would also be inside noise. So the choice between G and C is genuinely preference-based. I'll rule: G as default for behavioral/median reasons, C as acceptable alternative if the operator values right-tail; do NOT claim statistical superiority for either. Both only gray.

③ Remainder 2.5ATR trailing: Keep 2.5, reject 2.0 (evidence: 2.0 kills P90 27.9→19.4 and mean −1.5pp). Keep close-based peak update (the fix), ratchet-only. Parameter note: consider 2.5ATR on ATR(14) daily; do not tighten below 2.25; optionally widen to 3.0 for the news-channel entries (gap risk) — but no data → keep 2.5 uniform for now. Also: the peak should be close-based high-water mark; stop = peak − 2.5×ATR_at_previous_close; only ratchets.

Also there's a subtlety: layer ① B uses "浮盈≥15% → 保本" and layer ② G "+20% 卖半" — sequencing: at +15% stop to cost; at +20% sell half; remainder trails. Interaction: after selling half at +20%, does the cost-lock stay? Yes, lock applies to remainder. Fine.

One more interaction check: B's lock at +15% could stop out the remainder at cost before +20% is reached (reversal between +15% and +20%) — that's exactly the 4 saved trades; but it also could prevent some +20% partial sells from ever happening (trade hits +15%, reverses to cost, exits; without B it might have dipped and then run to +20%+). Net effect measured: ≈0. OK.

Q4: Realistic validation path for a personal sim that can't expand sample:

Key insight: with 20 stocks × 1 year, statistical selection between close variants is impossible; the goal shifts to (i) falsify dangerous variants, (ii) stress the chosen rule across regimes within existing data, (iii) live paper forward-test with pre-registered metrics.

Concrete path:
1. Within-sample robustness (cheap, do now):
 - Split by year: 2025 (n≈14) vs 2026 (n≈45); report G-A paired diff in each; require same sign, not significance.
 - Split by stock: leave-one-stock-out (20 folds): does G stay ≥ A in 80%+ of folds? LOBO (leave-one-stock-out) is the right resampling under cross-stock correlation better than i.i.d. bootstrap.
 - Regime tag: label each trade by market index regime (e.g., HS300 above/below 60-day MA at entry); report per-regime.
 - Tail metrics for all finalists: worst trade, P5, P90, max drawdown of trade equity; require B's tail numbers.
 - Limit-day audit: count simulated stop-exits on days the stock closed at limit-down or opened gapped below stop; if >3/59, add a "limit-down → exit next open" rule and re-run.
2. Pre-registration before gray: write down G's exact rule, the review date (3 months), the metrics (activation rate of take-profit legs, median capture on MFE≥15% trades, % exits that are stops, slippage vs 12bp assumption), and switch/kill criteria (e.g., if TP leg activation <20% of eligible trades or stop share of exits still >80%, kill). This converts the live sim into the true out-of-sample test.
3. Data hygiene going forward: log per-channel tags so the next 3-month review can test criticism 3; log every fill vs simulated price to calibrate slippage.
4. Optional cheap pseudo-OOS: use the backtest only to exclude the worst (E-style no-stop: worst −68.6% is disqualifying regardless of mean), then decide G/C by structure, not by 0.2-0.4pp point estimates.
5. Bootstrap refinements: cluster bootstrap by stock (resample stocks, keep their trades) → honest CI; expect CI to widen; if G-A still straddles 0 (it will), that's the final word: cannot select on this data.

Also note: the 3-month live review will generate maybe how many trades? 12 positions, holding periods? If average hold ~20-30 days, 12 slots → ~120-180 trades/year → ~30-45 in 3 months — enough to check *behavioral* metrics (activation rates) but still underpowered for mean differences. So set the review bar on process metrics, not P&L differences.

Q5: Final verdict + parameter list:

Verdict: 有条件通过 (conditional pass) — gray release only, with conditions:
- Layer ①: adopt B with threshold +15% (close-based high-water), stop→max(cost, current stop) — conditional on reporting B's P5/worst/P90 and a lock-threshold check {10,15,20}; if the check shows P90 loss > 0.5pp vs A, move lock to cost−0.5ATR or drop. Hmm, I need to give concrete parameters now. I'll say: adopt 15%, but require the two missing numbers (B's P90/P5) and the 10/15/20 threshold check within the same 59-trade framework before it goes from gray to default; gray can start now.
- Layer ②: G = +20% sell 50%, one-shot per trade, T+1-eligible only, remainder trails at 2.5ATR. C = +30/+50 sell 1/3 each. Ruling: gray with G as primary (median +5.67 vs C +3.10; one trigger vs two; matches the behavioral diagnosis), C as backup; pre-register the 3-month switch test. Alternatively G for all channels. Do not run G and C simultaneously on the same book to "compare" — actually, why not? A/B within the sim: assign by trade (alternate) could give a live comparison — with ~30-45 trades in 3 months, still underpowered for means but fine for activation rates. Hmm — I'd recommend: gray G on all, but tag; do not split A/B because n too small; keep C as pre-registered fallback.
- Layer ③: keep 2.5ATR close-based ratchet; forbid 2.0; do not replace partial selling with tighter trail (evidence given).
- Kill/switch criteria: e.g., after 3 months: TP first-leg activation rate < 20% among trades reaching +15% → threshold too high, lower to 15%... wait G's trigger is +20% for the partial sell; layer① locks at +15%. Among trades whose MFE≥20%, if median capture < cost+10%... let me set simpler: (a) 若浮盈≥15%的交易的最终中位实现收益 < +2%（即保本锁没起作用）; (b) 止损占卖出比例仍 > 70%; (c) 实际滑点 > 25bp 中位 → 停下来修执行而不是改参数.
- Reporting requirements: add missing metrics (G's P90, B's tail, per-year split, limit-day count); include D/E in the report archive for completeness; cluster bootstrap by stock re-run of G-A CI (expect wider; if upper bound still < ~1.5pp, treat G vs A as equivalent and rely on behavioral rationale).

Also, note the residual honesty point: the corrected backtest's own numbers show the whole exercise is at the noise floor: A's CI [+3.1,+10.7] — the strategy's mean edge itself is only known to be within [3.1,10.7]; the differences between exit variants (0.2-0.6pp) are an order of magnitude smaller than the CI width. So the final decision is necessarily governance/behavior-based, which is exactly what gray release + process metrics is for. State this clearly.

One more check on Q1's specific question: "一审也断言'46笔应为25笔'——请核对材料③修正后是否已对齐" → Material ③: "修正MFE子集（截至出场）：MFE≥20%的25笔" → yes, 25 = 25, aligned. But note: the auditor's claim was that the *label* was wrong (46 mislabeled); the corrected MFE-at-exit definition yields 25, matching. However, one could quibble: the original 46 might have been "MFE over full path including post-exit" vs "up to exit" — the corrected definition (截至出场) is the correct one for evaluating exit rules. So auditor right, now aligned. Also note the corrected MFE subset numbers (A 19.0 / C 20.5 / G 19.5) — spread 1.5pp on 25 trades, no CI → weak; don't over-read.

Also double-check the auditor's SE claim more carefully because I want to quantify precisely:
- p̂=0.59, n=59: SE = sqrt(0.59×0.41/59) = sqrt(0.2419/59) = sqrt(0.004097) = 0.0640 → ±6.4pp. 95% CI half-width = 1.96×6.4 ≈ 12.5pp. Auditor said "胜率SE±12pp" — that's the CI half-width mislabeled as SE. For the comparison 59% vs 63% (unpaired): SE_diff = sqrt(0.59×0.41/59 + 0.63×0.37/59) = sqrt(0.004097+0.003953) = sqrt(0.00805) = 0.0897?? wait: 0.63×0.37 = 0.2331, /59 = 0.003951. Sum = 0.008048, sqrt = 0.0897 → 9.0pp unpaired; paired (McNemar) would depend on discordants; with ~20% discordant maybe SE_diff ~4-6pp. Either way 4pp difference not significant. Substance holds.

Also check "G-A配对差+0.20pp，CI[-0.94,+1.29]" — plausibility: fine.

And check the "参数敏感性 +6.5~+7.9" claim vs per-config SE: per-trade return std — we can estimate from CI: A mean 6.70, CI [+3.1,+10.7] → half-width 3.8 → SE ≈ 1.94pp (percentile bootstrap, so ~±1.9pp). Grid spread 1.4pp < 2×SE → indeed indistinguishable. Good, that's the quantitative reconciliation: the plateau width (1.4pp) is smaller than the measurement resolution (~±1.9pp SE per config), so "plateau" is what noise-flat data looks like; it's evidence against *fragility* (no cliff), not evidence of *optimality*.

Also reconcile the two statements formally: "G−A不显著" (point +0.20, CI [−0.94,+1.29]) and "网格平台 +6.5~+7.9": both are consistent with a world where G's true edge is anywhere in ~[−1,+1.3]; the grid says configs in that family don't blow up; neither says G beats A. The correct joint conclusion: 参数不敏感（无脆弱性）+ 优势不可证明（无显著性）→ 选择权交还给运营标准（中位数/行为/简单性），并以下一轮真实流水做最终裁决. Also add: the plateau test varies G's parameters but not the *structure* vs A — i.e., the grid cannot rescue significance; it only de-risks the choice within G-family.

Also, potential new problem in corrected version: mean for A rose from 5.10 to 6.70 after "fixes + costs" — net +1.6pp. Costs −0.24pp round trip (12bp×2 on full position... actually only on what's traded; A trades once → −0.24pp). So the peak-fix added ~+1.8pp mean. That's a big model-change effect — meaning the original backtest's conclusions were materially sensitive to the look-ahead bug (in the conservative direction, luckily). Worth stating: this validates criticism 5 as the single highest-impact audit point; and it also warns that remaining model choices (exit at stop-level fill, flat slippage) could still move results by ~1pp — same order as the G-A gaps being debated. Quantify: if limit-down non-fill affects even 3 of 59 stop exits with −5pp each → mean −0.25pp; gap-through opens already handled by min(open,stop). Hmm, min(open, stop) handles gaps: if open < stop, fill at open — that's realistic for gaps (except limit-down lock where open = limit and no fill). So the main residual is limit-down days. Frequency in a momentum watchlist over a year: maybe 1-5% of exit events. Effect on means: small (<0.3pp), on tails: could worsen worst trade. So residual risk modest but should be counted (audit as diagnostic).

Also B "救回4笔" vs mean +0.03pp: the 4 saved trades were probably near-cost losses (−1 to −3%), and symmetric losses: early exits at lock cost some trades that would have run (they exited at cost but A would have held through and exited higher). 4 saves × ~2% / 59 ≈ +0.14pp, offset by ~0.1pp from capped recoveries ≈ +0.03pp net. Consistent.

Another subtle check: B's win rate 59% vs A 58% — +1 trade; but 4 trades "saved from gain-to-loss" — if they were floating-gain-then-loss trades under A (losses), converting to ~0 (breakeven, maybe −cost → slightly negative after costs, so still losses?) then they'd remain losses → win rate unchanged? "救回4笔浮盈变亏损" — saved from becoming losses, i.e., under A they ended as losses, under B they end ≈0. If ≈0 counts as neither win nor loss or as win at 0? Win rate went 58→59 (+0.6 trades?) — rounding. Fine, don't over-dig. Actually if 4 trades saved but win rate only +1pp (0.59 trades), then under B those 4 end between 0 and win threshold... maybe exit at cost = 0% counted as non-win → win rate same, but the 59% vs 58% diff is rounding of a different trade flipping. Minor inconsistency, worth a one-line note: B's "saves" mostly land at ~0%, not wins — so its value is loss-avoidance, not win-creation; check whether 0% exits counted as wins (definitional hygiene).

Hmm, wait: if 4 trades went from loss (A) to ~0 (B), win rate should be identical (unless 0% counts as win). Reported 58→59 suggests either one additional flip elsewhere or 0% counted as win. Flag as definitional check: does exit at cost count as win? With 12bp costs, "breakeven" exit is actually −0.12% → a loss. Minor but should be pinned down.

Now, also answer whether any of the auditor's points were "过度" (excessive): 
- Point 2's demand to fix pool bias: unfixable in-scope; the demand for full-market backtest is excessive for the context, but the *flag* is valid; mitigation = forward test + explicit labeling of scope.
- Point 3: valid flag, but "different exits per channel" is a research program, not a blocker for a common exit floor; resolution = per-channel tagging in gray phase.
- Point 4: valid, but for exit rules the portfolio coupling is weak; a cheap concurrency stat suffices.
- Point 10's walk-forward with 59 trades: statistically vacuous (would need to carve 59 trades into folds of ~10 → noise); excessive. Sensitivity grid + cluster bootstrap are the proportionate substitutes; those were done (though i.i.d., not clustered).
- Point 1's SE mislabel (minor).
- Point 8's "MFE不能用于选参数" over-broad (minor, principle-level).

Auditor's own errors summary:
(a) SE±12pp mislabel (actual SE ±6.4pp; ±12pp = 95% CI half-width).
(b) Claim 7's arithmetic: one extra half-sell costs ~6bp of position (0.06pp on trade return), not ~1pp; the gross gap couldn't be "eaten by one sell's cost". (The correct statement: the gap is within sampling noise, not that transaction costs eat it.)
(c) MFE ban over-broad: as-of-exit MFE subset is a legitimate diagnostic; forbidden only as a live gating variable.
(d) Demanding walk-forward/full-market at n=59 with a fixed 20-stock universe is infeasible and statistically empty; proportionate alternatives exist.
Otherwise the auditor's 10 points substantially stand: directionally correct; the correction round vindicated 5,6(partial),7(conclusion),8(number),9.

Also check one number: the auditor said "59% vs 63%" for A vs G — original: A 59, G 63 ✓.

Also "2026占45/59" — given window 2025-08~2026-08 (13 months ≈ 268 trading days? they said 250), 45/59 = 76% in the last ~62% of calendar. Not addressed by corrector. Keep as open item with cheap fix (year split).

Now assemble the parameter-level final list:

最终裁决：有条件通过（灰度），通过条件+参数清单：

Layer ①保本锁：
- 采纳（灰度即启）：触发=收盘价口径浮盈≥15%（用收盘 high-water，避免盘中噪声触发）；触发后止损线=max(原线, 成本价)；只升不降维持。
- 待补证据后转正：补 B 的 P5/最差/P90；锁阈值 {10,15,20} 三点检验（同一59笔框架）。
- 若 P90 相对 A 下降 >0.5pp → 改为成本+0.5×ATR（半保本）或弃用。
- 记账口径：成本价出场的交易记为亏损（含12bp成本）或单列，不许算赢。

Layer ②分批：
- 首选 G：浮盈≥20%（收盘口径确认，次日执行卖出，符合T+1）→ 卖50%；每笔最多触发一次；余仓走③。
- 备选 C：+30% 卖1/3、+50% 再卖1/3（余1/3走③）；仅当操作者明确偏好右尾时选C。
- 禁止同时跑G和C比较（n不足，只会产生伪结论）；3个月后按预注册标准二选一定版。
- 补报：G 的 P90/最差值（目前缺失，这是G的代价项，必须看到才能定版）。

Layer ③余仓ATR：
- 保持 peak(收盘) − 2.5×ATR(14, 前一日值)，只升不降；触发价=min(开盘, 止损线) 成交，加跌停日不可成交规则：若当日跌停或开盘即一字跌停 → 顺延至次日可成交价。
- 禁止 2.0ATR（证据：P90 27.9→19.4，杀肥尾）；2.5为下限。

组合层（新增，之前缺失）：
- 任何时点止盈卖出释放的资金，T+1内不得再进场（防churn）… actually not necessarily. Hmm, for sim maybe not needed. Keep it light: 并发持仓≥10时新信号降半仓 — 这属于入场体系，不在此裁决范围，但要求记录并发数分布。

灰度验收（3个月，预注册）：
- 过程指标：①止盈腿触发率 ≥40%（浮盈≥15%的交易中）②卖出中止损占比 ≤60%（现状100%）③首腿平均实现滑点 ≤25bp。
- 结果指标（仅监察不作决策）：G−A式配对比较无法在30-45笔上定论，只看方向一致性。
- Kill规则：若3个月内浮盈≥15%交易的中位实现收益 <0 → 保本锁失败，回退；若止损占比仍>70% → 问题在入场不在止盈，停止参数迭代。

Also required re-runs before定版 (all cheap, same data): cluster bootstrap (by stock) for G−A; year-split; lock-threshold {10,15,20}; limit-day count; B tail metrics; G's P90. That's ~6 numbers.

Let me also quantify the "effective sample" point: 20 stocks → ICC-based design effect: if within-stock correlation of outcomes ρ≈0.3, DEFF = 1+(m−1)ρ with m≈3 per stock → DEFF≈1.6 → effective n≈37; with ρ=0.5 → effective n≈27. Consistent with auditor's 20-30 estimate. Use this to state the detectable effect: at effective n≈30, paired-diff SE ~ maybe 1.3-1.5pp → 80% power detects ~2-2.5pp. So G−A=+0.2pp was never detectable. Good quantification.

Now the answer structure — the user asked 5 numbered questions, wants direct, quantitative, no fluff, Chinese. Use headers per question, tables where useful, concise bullets. Should be reasonably thorough but not bloated. Given the complexity, a fairly long answer is justified, but keep it tight. Use markdown headers and a table for Q1's 10 items. Let me draft.

Q1 table: 10 rows, columns: 一审批评 / 裁定 / 量化备注.

1. n=59小、胜率SE±12pp → 成立但数字错标：SE=√(0.59×0.41/59)=±6.4pp，±12pp是95%CI半宽。结论不变：4pp差异<1个SE，噪声。有效样本20-30与DEFF估算一致。
2. 票池偏差+单年 → 成立，且未修复（修正版没做年度拆分）。过度点：全市场池要求超出场景可行性。
3. 入场代理≠四通道 → 成立且本质不可回测修复，只能靠灰度期打通道标签。过度点：不应作为"不同通道不同止盈"的阻断条件，先上统一出场地板。
4. 组合层缺失 → 成立但权重低：出场规则组合耦合是二阶的；补一个并发持仓分布统计即可（未做）。
5. 未来函数 → 成立，且是全部批评中影响最大的一条：修正后A均值+5.10→+6.70（扣成本后，即peak修正贡献约+1.8pp毛），证明原bug实质性压低结果。修正方式（收盘后更新peak）正确。
6. 跌停/T+1/滑点 → 部分成立：T+1已补；12bp单边成本已加；但跌停不可成交未模拟，min(open,stop)在跌停日高估可成交性。残留风险量级：若59笔中1-5%出场遇跌停，均值影响<0.3pp，但对最差一笔的尾部影响更大。要求补"跌停日出场频次"诊断。
7. 无成本 → 结论成立（必须成本后报告），但算术错误：G相对A多一次半仓卖出，成本=12bp×50%=6bp≈0.06pp，不可能"吃掉1pp"。真正的问题是1pp差距在噪声内，不是被成本吃掉。修正后G−A成本后仍+0.20pp，佐证。
8. MFE → 数字对齐确认：修正版"截至出场MFE≥20% = 25笔"，与一审断言的25笔完全一致，一审数字判断正确。但"MFE是未来信息不能用于选参数"过度：截至出场的MFE是出场时点已实现量，用于事后条件对比（同一批路径上比较A/C/G的捕获率）是合法诊断；只有把MFE当实时决策门控才是偷看。修正版用法（诊断）合规。残留：25笔子集上A19.0/C20.5/G19.5，1.5pp差距无CI，无决策价值。
9. C漏报 → 成立，已修复。但新问题：修正版把D、E从报告中丢掉了（E的最差-68.6%是排除"不止损"方案的关键证据），完整性回退。
10. 建议 → 部分过度：n=59做walk-forward是空转（每折~10笔=噪声）；全市场池超出个人模拟盘约束。已做的Bootstrap+敏感性网格是对等替代，但用了iid bootstrap，未按票聚类，CI偏窄。

一审自身错误汇总：①SE/CI混标；②"成本吃掉1pp"算术错一个数量级；③MFE禁令过宽；④walk-forward要求在此样本下统计上空洞。这些不动摇其主结论（参数否决、灰度上线），一审方向正确。

Q2: 修正版统计处理的漏洞：

a) Bootstrap未按票/时间聚类：59笔来自20只票+时段集中（2026占76%），iid重采样低估相关性→CI[-0.94,+1.29]偏窄。按票聚类（resample 20只票）后上界可能从+1.29走到+1.5~2pp，但结论（含0）不变，只是"不显著"更确定。
b) 百分位bootstrap在偏态收益+小样本下欠覆盖，应BCa或至少报告。
c) 网格"平台"的证据强度：各格共享同59条路径，12格均值彼此高相关；且每格SE≈±1.9pp（由A的CI[+3.1,+10.7]反推半宽3.8→SE≈1.9），平台全宽1.4pp<1个SE——"平台"与"什么都测不出来"不可区分。
d) 调和：两个证据不矛盾，是同一条信息("噪声地板")的两面。CI含0 = 无法证明G优于A；平台 = 无法证明G的任何参数更差。合起来→数据只支持"该参数族不脆弱"，不支持"该参数族更优"。因此参数选择依据必须换成非统计标准（中位数/行为改造/执行简单性），最终裁决权交给灰度期真实流水。
e) 检验力硬约束：有效n≈27（DEFF≈1.6-2），配对差SE≈1.3-1.5pp，80%功效只能检出≥2pp的差异。G−A=+0.2pp、C−G=+0.4pp永远测不出。回测在这里是"证伪工具"（排除E类不止损：最差-68.6%；排除ATR2.0收紧）不是"选优工具"。
f) 报告缺口：G的P90缺失（G的全部卖点是中位数，其代价必然在右尾，必须看到才能定版）；B缺尾部指标；数字不一致一处（"2.5的+6.58/+27.8"与A的+6.70/+27.9对不上，需注明那是哪个方案）。
g) 定义卫生：成本价出场算不算"胜"？（B救回4笔但胜率只+1pp，疑似0%出场计入了胜）须固定口径：含费后<0记亏损。
h) 跌停出场未模拟（同Q1-6）。

Q3: 三层仲裁：

①保本锁15%：有条件采纳（灰度即启，转正前补证据）。
- 理由：成本后均值+0.03pp≈零，胜率+1pp，救回4笔——它不是收益增强器，是行为保险（把"浮盈变亏损"这一类的下尾剪掉）。这类改动可以用"均值中性+分布改善"标准通过，但分布证据目前缺失（B无P5/最差/P90）。
- 参数：触发=收盘浮盈≥15%（不用盘中，防噪声）；触发后线=max(现线,成本)，只升不降；与③共存。
- 转正条件：补B尾部三项；锁阈值{10,15,20}三点同框架检验；若B相对A的P90损失>0.5pp→改为成本+0.5ATR或弃。记账：成本价出场计为亏损或单列。

②分批G vs C：裁定G为灰度首选，C为预注册备选，两者均不得宣称统计优势。
- 依据（非统计）：G中位+5.67 vs C+3.10（对"从不止盈到开始止盈"的行为改造目标，中位数=多数交易的体感）；G一次触发vs C两次（执行简单、滑点敞口小）；G首腿+20%比C的+30%更早兑现，与"挂载率≈0"病灶对症。C的优势（均值+7.31/P90+29.1，MFE≥20%子集+20.5最高）是右尾逻辑，量级0.4pp，在噪声内。
- 参数：G=收盘确认浮盈≥20%→次一交易日卖50%（T+1合规），每笔至多一次；余仓走③。C=+30%卖1/3、+50%卖1/3，余1/3走③。
- 定版规则（预注册）：3个月后，若浮盈≥20%交易的捕获中位数与P90均不差于C组…实际上不能同跑。改为：3个月复核时若止损占比仍>60%或首腿触发率<40%（浮盈≥15%交易中），切C或回炉。
- 前置缺口：必须先补G的P90与最差值。若G的P90比A低>3pp，则C升为首选。

③余仓2.5ATR：采纳，参数冻结。
- peak改为收盘后更新=正确修复，保留。
- 拒绝2.0：P90 27.9→19.4、均值−1.5pp，实锤杀肥尾；也拒绝"收紧ATR替代分批"。
- 补一条执行规则：跌停/一字跌停无法成交→顺延次一交易日，出场价按可成交口径记。
- ATR用前一交易日值计算当日触发线（避免当日ATR自引用）——修正版未明说，要求写死。

Q4: 现实验证路线（20票×1年，无法扩样本）：

定位转换：回测只做三件事——证伪危险方案、参数不脆弱性检查、执行细节压力测试；"哪个更好"交给前向。

具体清单（全部用现有数据，工作量~1-2天）：
1. 按票聚类Bootstrap重算G−A、C−A的CI（预期变宽30-60%，若上界仍<2pp，定案"不可分辨"）。
2. 年度切分：2025(n≈14)/2026(n≈45)分别报G−A方向；要求同号，不要求显著。
3. Leave-one-stock-out：20折，G≥A的折数≥16/20（80%）为通过线。
4. 尾部三件套补全：所有方案的worst/P5/P90（尤其B和G）。
5. 跌停诊断：统计模拟出场日中收盘跌停或开盘一字跌停的笔数；≥2笔则加顺延规则重跑。
6. 滑点二档敏感性：止损类出场按30bp、止盈类按12bp重算一遍（成本后均值变化<0.3pp即通过）。
7. 并发持仓分布：59笔的重叠度直方图（检验组合层耦合的量级）。
之后：3个月灰度=真正的样本外。预注册验收指标（过程指标为主，因为30-45笔对均值仍无功效）：
- 止盈首腿触发率（浮盈≥15%交易中）≥40%；
- 卖出中止损占比从100%降到≤60%；
- 首腿执行滑点中位≤25bp；
- kill线：若浮盈≥15%交易的中位实现收益<0 → 保本锁回滚。
未来数据卫生：入场打channel标签（修复批评3）、记录每笔模拟价vs可成交价（校准滑点假设）。

Q5: 最终结论：有条件通过（灰度上线，禁止全量），条件+参数清单：

裁决表：
- 保本锁15%：灰度即启；转正前补B尾部+阈值三点检验。
- 分批：G（+20%卖50%）灰度首选；C备选；禁止同跑对比；G的P90补报后复核。
- 余仓2.5ATR收盘peak：采纳冻结；禁止2.0；加跌停顺延条款。
- ATR2.0、