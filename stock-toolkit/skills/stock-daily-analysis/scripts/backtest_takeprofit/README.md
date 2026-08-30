# 止盈方案回测与双层审计存档（2026-08-30）

- backtest_schemes.py：A/B/C/D/G 方案对比（59笔/20票/一年，成本后+Bootstrap CI+参数网格）
- validate_ultra.py：ultra 仲裁要求的全部验证（P90门槛/聚类Bootstrap/LOO折/分年/跌停诊断/滑点）
- audit_round1.md：一审审计意见（depu/llm-large-claude）
- audit_round2_ultra.md：二审仲裁（llm-ultra-claude）
- K线数据缓存 /tmp/kb/*.json 需重拉（news-web /api/stocks/<名> 的 kline 字段）
- 结论：C 方案定稿（+15%保本锁/+30%+50%各卖1/3/余仓2.5ATR），2026-11-30 灰度复核
