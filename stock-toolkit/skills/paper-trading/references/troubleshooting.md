# 常见问题与故障排除详解

本指南详细说明 paper-trading 的常见问题、错误信息和解决方案。

## 目录

- [安装问题](#安装问题)
- [资金池问题](#资金池问题)
- [交易问题](#交易问题)
- [价格获取问题](#价格获取问题)
- [K线数据问题](#k线数据问题)
- [股票搜索问题](#股票搜索问题)
- [新闻获取问题](#新闻获取问题)
- [命令找不到](#命令找不到)
- [数据损坏](#数据损坏)
- [网络问题](#网络问题)

---

## 安装问题

### 问题 1：命令未找到 - ptrade: command not found

**现象**：
```bash
$ ptrade init "股票"
ptrade: command not found
```

**原因分析**：
- 未成功安装 paper-trading
- 安装后的命令不在 PATH 中
- Python 环境问题

**解决方案**：

1. **确认已安装**：
```bash
cd paper-trading/scripts
pip list | grep paper-trading
```

2. **重新安装**：
```bash
cd paper-trading/scripts
pip uninstall paper-trading -y
uv tool install --editable .
```

3. **检查 PATH**：
```bash
which ptrade

# 如果找不到，检查 Python 安装路径
# uv tool 通常安装到 ~/.local/bin/
export PATH=$HOME/.local/bin:$PATH
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
```

4. **直接使用 Python 运行**（临时方案）：
```bash
cd paper-trading/scripts
python -m paper_trading.cli init "股票" --capital 100000
```

---

### 问题 2：uv tool install 失败

**现象**：
```bash
$ uv tool install --editable .
Error: uv is not installed
```

**原因**：未安装 uv 工具

**解决方案**：
```bash
# 安装 uv
pip install uv

# 验证安装
uv --version

# 重新安装 paper-trading
cd paper-trading/scripts
uv tool install --editable .
```

---

### 问题 3：依赖冲突

**现象**：
```bash
ERROR: pip's dependency resolver does not currently take into account all the packages...
```

**原因**：Python 环境中已有包版本冲突

**解决方案**：
```bash
# 使用虚拟环境
cd paper-trading/scripts
python -m venv venv
source venv/bin/activate

# 然后安装
pip install -e .
```

或使用 uv 创建虚拟环境：
```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

---

## 资金池问题

### 问题 4：资金池未初始化

**现象**：
```bash
$ ptrade buy "股票" --qty 100
错误：未找到股票 "股票" 的资金池
```

**原因分析**：
- 股票未初始化
- 股票名称拼写错误

**解决方案**：

1. **先初始化资金池**：
```bash
ptrade init "股票" --capital 100000
```

2. **查看已初始化的股票**：
```bash
ptrade list
```

3. **检查股票名称**：
```bash
# 确认股票名称正确
ptrade info "股票名称"
```

---

### 问题 5：可用资金不足

**现象**：
```bash
$ ptrade buy "股票" --amount 100000
错误：可用资金不足（需要 100000，当前可用 50000）
```

**原因**：资金池余额不足以支付交易金额

**解决方案**：

1. **减少买入金额**：
```bash
ptrade buy "股票" --amount 50000
```

2. **查看资金池状态**：
```bash
ptrade pool "股票"
```

3. **卖出部分持仓释放资金**：
```bash
ptrade sell "股票" --qty 50
```

4. **重新初始化更大资金池**（谨慎！）：
```bash
ptrade init "股票" --capital 200000 --force
```

---

### 问题 6：占用资金计算错误

**现象**：`account.json` 中的 `used_capital` 与持仓金额不一致

**原因**：数据文件损坏或计算错误

**解决方案**：

1. **检查 JSON 格式**：
```bash
cat intermediate/股票/模拟买卖/account.json | jq .
```

2. **重新初始化**（会清除数据）：
```bash
ptrade init "股票" --capital 100000 --force
```

3. **手动修复**（高级）：
```json
// 编辑 account.json
{
  "capital_pool": {
    "total_capital": 100000,
    "available_capital": 50000,
    "used_capital": 50000  // 手动纠正
  }
}
```

---

## 交易问题

### 问题 7：持仓数量不足

**现象**：
```bash
$ ptrade sell "股票" --qty 200
错误：持仓数量不足（当前持仓 100，请求卖出 200）
```

**原因**：卖出数量超过持仓数量

**解决方案**：

1. **查看持仓数量**：
```bash
ptrade holdings "股票"
```

2. **减少卖出数量**：
```bash
ptrade sell "股票" --qty 100
```

3. **全仓卖出**：
```bash
ptrade sell "股票" --all
```

---

### 问题 8：无法获取实时价格

**现象**：
```bash
$ ptrade buy "股票" --qty 100
错误：无法获取股票的实时价格
```

**原因分析**：
- 网络连接问题
- 股票代码错误
- API 暂时无法访问

**解决方案**：

1. **检查网络**：
```bash
ping www.qq.com
ping finance.sina.com.cn
```

2. **检查股票代码**：
```bash
ptrade fetch-price sh600000  # 测试有效代码
ptrade fetch-price 您的代码
```

3. **手动指定价格**（当前版本不支持）：
```bash
# 暂时修复：等待网络恢复
```

4. **验证 API 可用性**：
```bash
curl "https://qt.gtimg.cn/q=sh600000"
```

---

### 问题 9：重试等待超时

**现象**：
```bash
$ ptrade buy "股票" --qty 100
错误：请求超时，已重试 2 次
```

**原因**：网络延迟或 API 响应慢

**解决方案**：

1. **检查网络**：
```bash
ping finance.sina.com.cn
```

2. **稍后重试**：
```bash
# 等待几秒后重试
ptrade buy "股票" --qty 100
```

3. **更换网络环境**：
```bash
# 尝试使用 Wi-Fi 或切换网络
```

4. **检查代理设置**：
```bash
echo $http_proxy
echo $https_proxy
# 如果设置了代理，可能需要取消
export http_proxy=
export https_proxy=
```

---

## 价格获取问题

### 问题 10：股票代码格式错误

**现象**：
```bash
$ ptrade fetch-price abcdef
错误：无法识别股票代码格式
```

**原因分析**：
- A股代码必须以 sh 或 sz 开头 + 6 位数字
- 港股代码必须以 hk 开头 + 5 位数字
- 美股代码为字母（如 AAPL）

**解决方案**：

1. **确认代码格式**：
```bash
# A股
sh600000  # 上海证券交易所
sz000001  # 深圳证券交易所

# 港股
hk00700   # 腾讯

# 美股
AAPL      # 苹果
```

2. **使用 search 查询代码**：
```bash
ptrade search "股票名称"
```

---

### 问题 11：价格返回为空或 null

**现象**：
```bash
$ ptrade fetch-price sh999999
股票名称: null
当前价格: null
```

**原因**：股票代码不存在或已退市

**解决方案**：

1. **搜索股票代码**：
```bash
ptrade search "股票名称"
```

2. **查看官方信息**：
```bash
# 访问东方财富、同花顺等网站确认股票代码
```

---

## K线数据问题

### 问题 12：K线数据返回为空

**现象**：
```bash
$ ptrade fetch-kline sh999999 --type day --count 30
未找到 K线数据
```

**原因**：股票代码不存在或 API 限制

**解决方案**：

1. **验证股票代码**：
```bash
ptrade fetch-price sh600000  # 用有效代码测试
ptrade fetch-kline sh600000 --type day --count 10
```

2. **减少请求数量**：
```bash
ptrade fetch-kline sh600000 --type day --count 10
```

3. **更换 K线类型**：
```bash
ptrade fetch-kline sh600000 --type week --count 20
```

---

### 问题 13：K线时间范围限制

**现象**：请求大量K线数据时报错

**原因**：API 对请求的数据有限制

**解决方案**：

1. **分批获取**：
```bash
# 获取最近 100 根
ptrade fetch-kline sh600000 --type day --count 100
```

2. **使用用户管理的数据存储**：
```bash
# 导出数据后手动处理
ptrade export --format json > data.json
```

---

## 股票搜索问题

### 问题 14：搜索无结果

**现象**：
```bash
$ ptrade search "不存在的股票"
未找到相关股票
```

**原因**：关键词不匹配或股票不存在

**解决方案**：

1. **使用股票全称**：
```bash
ptrade search "AI-生成"
ptrade search "腾讯科技"
```

2. **使用代码搜索**：
```bash
ptrade search "00700"
ptrade search "600519"
```

3. **减少搜索关键词**：
```bash
ptrade search "茅台"  # 而不是 "贵州茅台集团股份有限公司"
```

---

## 新闻获取问题

### 问题 15：新闻返回为空

**现象**：
```bash
$ ptrade fetch-news
未获取到新闻
```

**原因分析**：
- 网络连接问题
- API 暂时不可用
- 数据源限制

**解决方案**：

1. **检查网络**：
```bash
ping www.cls.cn
ping finance.sina.com.cn
```

2. **更换新闻源**：
```bash
ptrade fetch-news --source cls
ptrade fetch-news --source sina
ptrade fetch-news --source tv
```

3. **重试**：
```bash
# 等待几秒后重试
ptrade fetch-news --source all --limit 10
```

---

### 问题 16：TradingView 新闻获取失败

**现象**：
```bash
$ ptrade fetch-news --source tv
错误：无法连接到 TradingView API
```

**原因**：TradingView API 可能受系统安全策略限制

**解决方案**：

1. **使用其他新闻源**：
```bash
ptrade fetch-news --source cls
ptrade fetch-news --source sina
```

2. **检查代理设置**：
```bash
# TradingView 可能需要代理
export http_proxy=your_proxy
export https_proxy=your_proxy
```

---

## 命令找不到

### 问题 17：Python 命令找不到

**现象**：
```bash
$ ptrade
ptrade: command not found

$ python
python: command not found
```

**原因**：Python 未安装或不在 PATH 中

**解决方案**：

1. **检查 Python 安装**：
```bash
python --version

# 或
python3 --version
```

2. **安装 Python**：
```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip

# macOS
brew install python3

# Windows
# 从 python.org 下载安装
```

3. **添加到 PATH**：
```bash
# 编辑 ~/.bashrc
export PATH=/usr/local/bin:$PATH
source ~/.bashrc
```

---

### 问题 18：pip 命令找不到

**现象**：
```bash
$ pip install uv
pip: command not found
```

**解决方案**：

1. **使用 pip3**：
```bash
pip3 install uv
```

2. **使用 python -m pip**：
```bash
python3 -m pip install uv
```

3. **安装 pip**：
```bash
# Ubuntu/Debian
sudo apt-get install python3-pip

# macOS
# pip 通常随 Python 一起安装
```

---

## 数据损坏

### 问题 19：JSON 解析错误

**现象**：
```bash
$ ptrade info "股票"
错误：无法解析 account.json 文件
```

**原因**：JSON 文件格式错误或损坏

**解决方案**：

1. **检查 JSON 语法**：
```bash
cat intermediate/股票/模拟买卖/account.json | jq .
# jq 会指出语法错误
```

2. **手动修复**：
```bash
# 用文本编辑器打开并修复
# vim
vim intermediate/股票/模拟买卖/account.json

# 或用其他编辑器
nano intermediate/股票/模拟买卖/account.json
```

3. **重新初始化**：
```bash
ptrade init "股票" --capital 100000 --force
```

---

### 问题 20：数据文件丢失

**现象**：
```bash
$ ptrade info "股票"
错误：找不到 account.json 文件
```

**原因**：误删除或移动文件

**解决方案**：

1. **检查目录结构**：
```bash
ls -la intermediate/股票/模拟买卖/
```

2. **从备份恢复**：
```bash
# 如果有备份
cp backup.json intermediate/股票/模拟买卖/account.json
```

3. **重新初始化**：
```bash
ptrade init "股票" --capital 100000 --force
```

---

## 网络问题

### 问题 21：无法连接到 API

**现象**：
```bash
$ ptrade fetch-price sh600000
错误：无法连接到价格获取接口
```

**原因**：网络故障或防火墙限制

**解决方案**：

1. **检查网络连接**：
```bash
ping 8.8.8.8
ping www.qq.com
```

2. **检查 DNS**：
```bash
nslookup www.qq.com
```

3. **检查防火墙**：
```bash
# Ubuntu/Debian
sudo ufw status
# 如果需要，临时关闭测试
sudo ufw disable
```

4. **使用代理**：
```bash
export http_proxy=http://your_proxy:port
export https_proxy=http://your_proxy:port
```

---

### 问题 22：SSL 证书错误

**现象**：
```bash
$ ptrade fetch-price sh600000
错误：SSL certificate verify failed
```

**原因**：SSL 证书问题

**解决方案**：

1. **更新证书**：
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install ca-certificates

# macOS
brew install ca-certificates
```

2. **临时禁用 SSL 验证**（不推荐，仅用于测试）：
```bash
# 修改源代码，在请求时添加 verify=False
# 生产环境不推荐这样做
```

3. **检查系统时间**：
```bash
date
# 确保时间是正确的
```

---

## 获取帮助

### 获取命令帮助

```bash
# 查看所有命令
ptrade --help

# 查看特定命令帮助
ptrade init --help
ptrade buy --help
ptrade fetch-price --help
```

### 查看版本

```bash
ptrade version
```

### 报告问题

如果遇到本文档未涵盖的问题：

1. **收集错误信息**：
```bash
ptrade command 2>&1 > error.log
```

2. **检查环境**：
```bash
python --version
pip list | grep paper-trading
```

3. **联系支持**：
   - 查看 GitHub Issues
   - 提交 Issue 并附上错误信息

---

## 预防措施

### 定期备份

```bash
# 自动备份脚本
#!/bin/bash
DATE=$(date +%Y%m%d)
BACKUP_DIR=~/backups/paper_trading

# 创建备份目录
mkdir -p $BACKUP_DIR

# 备份数据
cd intermediate
tar -czf $BACKUP_DIR/backup_$DATE.tar.gz */

# 清理 30 天前的备份
find $BACKUP_DIR -name "backup_*.tar.gz" -mtime +30 -delete
```

### 数据导出

```bash
# 导出所有数据
ptrade export --format json --output data_$(date +%Y%m%d).json
```

### 定期测试

```bash
# 测试基本功能
ptrade list
ptrade info
ptrade version

# 测试市场数据
ptrade fetch-price sh600000
ptrade fetch-news --limit 5
```

---

## 相关文档

- **[数据存储结构](data-storage.md)** - 了解数据文件结构
- **[基础交易操作](basic-operations.md)** - 交易命令详解
- **[查询命令说明](query-commands.md)** - 查询命令帮助
- **[数据管理](data-management.md)** - 数据导出和备份
