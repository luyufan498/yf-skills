# 路径重构迁移指南

## 变更说明

从版本 1.x 到 2.0，脚本的目录结构进行了重构：

### 旧结构（已废弃）
- 账户数据: `intermediate/{股票名称}/模拟买卖/`
- 分析报告: `intermediate/stocks_analysis/{股票名称}/`
- 环境变量: `STOCK_INTERMEDIATE_DIR`, `STOCK_ANALYSIS_WORKSPACE`

### 新结构（推荐）
- 账户数据: `tradings/{股票名称}/`
- 分析报告: `workspace_root/stocks_analysis/{股票名称}/`（直接在工作空间根目录）
- 环境变量: `STOCK_ANALYSIS_WORKSPACE`（仅此一个）

## 迁移步骤

如果您有旧版本的数据，可以手动迁移：

1. 备份现有数据
   ```bash
   cp -r intermediate intermediate.backup
   ```

2. 创建新目录结构
   ```bash
   mkdir -p tradings
   mkdir -p stocks_analysis
   ```

3. 移动数据
   ```bash
   # 移动账户数据
   mv intermediate/*/模拟买卖 tradings/

   # 移动分析报告到工作空间根目录
   mv intermediate/stocks_analysis/* .
   ```

4. 验证数据
   ```bash
   python paper_trading.py account list
   python paper_trading.py analysis list
   ```

5. 删除旧目录（确认无误后）
   ```bash
   rm -rf intermediate
   ```

## 重要提示

- 此迁移需要手动执行，系统不会自动迁移旧数据
- 建议在迁移前创建完整备份
- 可以保留旧目录以便调试，直到确认新系统正常工作
