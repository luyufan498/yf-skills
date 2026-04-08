# 股票名称验证

## 概述

Paper-trading 提供内置的股票名称验证功能，确保用户在所有操作中使用合法的股票名称。

## 验证函数

### `validate_stock_name(stock_name: str) -> tuple[bool, Optional[str]]`

验证股票名称是否存在并返回对应的股票代码。

**参数:**
- `stock_name`: 要验证的股票名称（例如 "赛力斯"、"贵州茅台"）

**返回值:**
- `(是否合法, 股票代码)` 元组
  - `is_valid`: 如果股票存在则为 True，否则为 False
  - `stock_code`: 如果有效则为股票代码，否则为 None

**示例:**
```python
from paper_trading import validate_stock_name

is_valid, code = validate_stock_name("赛力斯")
if is_valid:
    print(f"找到: {code}")  # 例如 sh603527
else:
    print("未找到股票")
```

## 验证在哪些地方使用

### 1. 账号创建 (`ptrade init`)

创建新交易账户时，如果没有提供明确的股票代码，会验证股票名称：

```bash
# 这会验证 "赛力斯" 是否存在
ptrade init "赛力斯" --capital 100000

# 这会因验证失败而报错（无效名称）
ptrade init "不存在的股票123" --capital 100000
```

如果你提供 `--code`，则跳过验证：

```bash
# 跳过验证，使用提供的代码
ptrade init "任意名称" --capital 100000 --code sh000001
```

### 2. 分析报告保存 (`analysis` 模块)

保存分析报告时，如果 `validate_stock=True`（默认）：

```python
from paper_trading import AnalysisManager

manager = AnalysisManager()
# 这会验证股票名称
manager.save_analysis("赛力斯", "# 分析内容")
```

你可以禁用验证：

```python
manager = AnalysisManager(validate_stock=False)
manager.save_analysis("任意名称", "# 分析内容")  # 无验证
```

## 错误处理

### StockValidationError

用于股票验证错误的自定义异常：

```python
from paper_trading import StockValidationError, validate_stock_name

try:
    is_valid, code = validate_stock_name("Invalid Stock")
    if not is_valid:
        raise StockValidationError(f"未找到股票: Invalid Stock")
except StockValidationError as e:
    print(f"验证失败: {e}")
```

### ValueError 消息

当验证失败时，用户会看到清晰的错误消息：

```
❌ 股票名称 '不存在的股票123' 未能通过验证，请确保使用正确的股票名称
```

## 支持的股票类型

验证支持：
- **A股**: 上海 (sh)、深圳 (sz)、北京 (bj) 交易所
- **港股**: 香港股票 (hk 前缀)
- **美股**: 美国股票 (gb 前缀)

## API 来源

验证使用 [新浪财经 Suggest API](https://suggest3.sinajs.cn) 搜索股票信息。

## 测试

运行验证测试：

```bash
pytest tests/test_code_searcher.py -v
```

## 最佳实践

1. **始终验证** 涉及用户输入时的股票名称
2. **禁用验证** 仅用于测试或当你确定股票名称时
3. **优雅处理** 代码中的验证错误
4. **使用明确代码** 当你想跳过验证时（例如现有账户）
