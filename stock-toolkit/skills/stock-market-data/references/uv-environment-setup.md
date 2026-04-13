# UV 环境安装和使用指南

## 为什么需要 UV 环境？

本 skill 支持多个市场的股票数据获取，其中：

- **A股 (sh/sz/bj)** 和 **港股 (hk)**: 使用腾讯/新浪接口，**无额外依赖**
- **美股 (AAPL, TSLA 等)**: 使用 YFinance，**需要 yfinance 库**

只有在查询美股数据时才需要配置 UV 环境，其他功能可以直接使用 Python 运行。

## 功能依赖对比

| 功能 | 市场类型 | 外部依赖 | 是否需要 UV 环境 |
|------|----------|----------|------------------|
| 实时股价查询 | A股/港股 | requests | ❌ 不需要 |
| K线数据 (日K/周K/月K) | A股/港股 | requests | ❌ 不需要 |
| 分时数据/分钟K线 | A股/港股 | requests | ❌ 不需要 |
| 市场新闻查询 | 全市场 | requests | ❌ 不需要 |
| 股票代码搜索 | 全市场 | requests | ❌ 不需要 |
| K线数据 (日K/周K/月K) | 美股 | yfinance, pandas | ✅ **需要** |
| K线数据 (分钟K线) | 美股 | yfinance, pandas | ✅ **需要** |

## 安装步骤

### 1. 安装 UV（如果还没有安装）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

或使用 pip：
```bash
pip install uv
```

### 2. 创建虚拟环境

在 skill 根目录（包含 `SKILL.md` 的目录）执行：

```bash
cd .claude/skills/stock-market-data
uv venv
```

### 3. 安装美股数据依赖

```bash
uv pip install -e ./scripts
```

安装的依赖包括：
- `yfinance≥0.2.0`: 美股数据获取
- `pandas≥1.0.0`: 数据处理

## 使用示例

### 只用 A股/港股功能（无需 UV 环境）

```bash
# 直接使用 Python
python3 .claude/skills/stock-market-data/scripts/fetch_kline_data.py sh600000 -t day -c 5
python3 .claude/skills/stock-market-data/scripts/fetch_kline_data.py hk00700 -t week -c 10

# 查询实时价格
python3 .claude/skills/stock-market-data/scripts/fetch_realtime_stock.py sh600000 sz000001

# 查询新闻
python3 .claude/skills/stock-market-data/scripts/fetch_market_news.py -n 20

# 搜索股票代码
python3 .claude/skills/stock-market-data/scripts/search_stock_code.py 腾讯
```

### 使用美股功能（需要 UV 环境）

```bash
cd .claude/skills/stock-market-data

# 美股 K线数据
uv run python3 scripts/fetch_kline_data.py AAPL -t day -c 5
uv run python3 scripts/fetch_kline_data.py TSLA -t week -c 10
uv run python3 scripts/fetch_kline_data.py SPX -t day -c 3      # 指数
uv run python3 scripts/fetch_kline_data.py msft -t 60min -c 8 # 分钟K线

# 美股实时价格
uv run python3 scripts/fetch_realtime_stock.py AAPL TSLA
```

## 目录结构

```
.claude/skills/stock-market-data/
├── .venv/              # UV 虚拟环境（仅用于美股）
├── SKILL.md           # Skill 说明文档
├── scripts/
│   ├── pyproject.toml # 依赖配置
│   ├── fetch_kline_data.py    # K线数据（支持所有市场）
│   ├── fetch_realtime_stock.py # 实时价格（支持所有市场）
│   ├── fetch_market_news.py    # 市场新闻
│   └── search_stock_code.py    # 股票搜索
└── references/
    └── uv-environment-setup.md # 本文档
```

## 常见问题

### Q: 不安装 UV 环境会怎样？
A: 你可以正常使用 A股、港股的所有功能，包括实时价格、K线数据、新闻和搜索。只有查询美股数据时会提示缺少 yfinance 库。

### Q: UV 环境占多少空间？
A: 约 200-300MB，主要是 pandas 和 yfinance 的依赖包。

### Q: 可以只安装 yfinance，不使用 UV 吗？
A: 可以，但推荐使用 UV 因为：
- 更快的解析和安装速度
- 更好的依赖解析
- 与 skill 项目集成更好

### Q: 如何更新依赖？
A: 在 skill 根目录运行：
```bash
uv pip install --upgrade -e ./scripts
```

### Q: 如何删除 UV 环境？
A: 在 skill 根目录运行：
```bash
rm -rf .venv
```
删除后仍可继续使用 A股/港股功能。

## 推荐配置

如果你需要经常使用美股数据，建议：

1. **一次配置，长期使用**：按照上述步骤安装 UV 环境
2. **统一入口**：所有命令都使用 `uv run python3 scripts/...` 前缀
3. **定期更新**：偶尔运行 `uv pip install --upgrade -e ./scripts`

如果你只用 A股/港股数据，无需配置 UV 环境，直接使用 Python 运行即可。