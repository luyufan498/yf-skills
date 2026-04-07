# YF Skills - Claude Code Marketplace

欢迎使用 YF Skills Marketplace！这里包含了多个专业的 Claude Code 技能（Skills），涵盖财经分析、股票交易、文档构建等领域。

## 快速开始

### 安装 Marketplace

```bash
# 方法1：通过 GitHub（推荐）
/plugin marketplace add luyufan498/yf-skills

# 方法2：通过完整 URL
/plugin marketplace add https://github.com/luyufan498/yf-skills
```

### 安装单个技能

```bash
# 安装股票市场数据技能
/plugin install stock-market-data@yf-skills

# 安装模拟盘交易系统
/plugin install paper-trading@yf-skills

# 安装 NBL PPT 构建工具
/plugin install nbl-ppt-builder@yf-skills
```

## 可用技能

| 技能名称 | 描述 | 类别 | 安装命令 |
|---------|------|------|---------|
| stock-market-data | 中国A股、港股、美股实时股价和历史数据 | finance | `/plugin install stock-market-data@yf-skills` |
| paper-trading | 模拟盘交易系统，独立资金池管理 | finance | `/plugin install paper-trading@yf-skills` |
| stock-daily-analysis | 股票每日分析，市场新闻聚合 | finance | `/plugin install stock-daily-analysis@yf-skills` |
| nbl-ppt-builder | NBL 企业 PPT 构建工具 | productivity | `/plugin install nbl-ppt-builder@yf-skills` |
| searxng-search | SearXNG 增强搜索工具 | productivity | `/plugin install searxng-search@yf-skills` |
| siyuan-notes | 思源笔记命令行工具 | productivity | `/plugin install siyuan-notes@yf-skills` |
| gf-finance | 广发证券工具接口 | finance | `/plugin install gf-finance@yf-skills` |

## 更新 Marketplace

```bash
# 更新 marketplace 到最新版本
/plugin marketplace update yf-skills

# 更新所有已安装的技能
/plugin update
```

## 禁用/删除技能

```bash
# 禁用技能
/plugin disable skill-name@yf-skills

# 启用技能
/plugin enable skill-name@yf-skills

# 删除技能
/plugin remove skill-name@yf-skills
```

## 移除 Marketplace

```bash
/plugin marketplace remove yf-skills
```

## 技能使用示例

### Stock Market Data

```bash
# 查询实时股价
python3 scripts/fetch_realtime_stock.py sh600000

# 获取K线数据
python3 scripts/fetch_kline_data.py sh600000 -t day -c 30

# 获取市场新闻
python3 scripts/fetch_market_news.py -n 20
```

### Paper Trading

```bash
# 初始化模拟交易
ptrade --init

# 查看持仓
ptrade --portfolio

# 执行交易
ptrade buy sh600000 --quantity 100
```

### NBL PPT Builder

```bash
# 在对话中直接使用
"帮我创建一个季度业务汇报的 PPT"
"制作一个产品介绍演示"
```

## 配置

### Stock Market Data

```bash
# 美股数据需要配置（可选）
export SEARXNG_URL=http://your-searxng-server:38080
```

### Siyuan Notes

```bash
# 必需的环境变量
export SIYUAN_ENDPOINT=http://127.0.0.1:6806
export SIYUAN_TOKEN=your-api-token
```

## 故障排除

### 无法添加 Marketplace

1. 检查网络连接
2. 验证 GitHub 仓库是否可访问
3. 确认你有权限访问私有仓库（如果是）

### 技能安装失败

1. 检查技能源 URL 是否正确
2. 验证技能目录包含必需的文件（SKILL.md, plugin.json）
3. 对于需要外部依赖的技能，确保环境已配置

### 更新失败

```bash
# 手动清理缓存
rm -rf ~/.claude/plugins/marketplaces/yf-skills

# 重新添加 marketplace
/plugin marketplace add luyufan498/yf-skills
```

## 开发者信息

### 本地测试 Marketplac

```bash
# 克隆仓库
git clone https://github.com/luyufan498/yf-skills

# 从本地目录添加
/plugin marketplace add ./yf-skills

# 验证配置
/plugin validate ./yf-skills
```

### 技能结构

每个技能包含：

```
skill-name/
├── .claude-plugin/
│   └── plugin.json      # 技能配置
├── SKILL.md             # 技能定义
├── scripts/             # 脚本文件
└── README.md            # 使用说明
```

### 验证技能配置

```bash
# 验证单个技能
claude plugin validate ./skill-name

# 验证整个 marketplace
claude plugin validate .
```

## 版本

当前版本: 1.0.0

## 开源协议

MIT License

## 联系方式

- GitHub: https://github.com/luyufan498/yf-skills
- Issues: https://github.com/luyufan498/yf-skills/issues

## 相关文档

- [Claude Code Plugin Documentation](https://code.claude.com/docs/zh-CN/plugin-marketplaces)
- [创建 Plugins](https://code.claude.com/docs/zh-CN/plugins)
- [发现和安装 Plugins](https://code.claude.com/docs/zh-CN/discover-plugins)
