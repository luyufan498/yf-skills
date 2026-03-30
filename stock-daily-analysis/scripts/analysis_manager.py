#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票分析报告管理器
用于保存、读取和管理股票分析结果

目录结构：
workspace/
├── 赛力斯/
│   ├── 赛力斯-2026-03-08-1630.md
│   └── 赛力斯-2026-03-07-1000.md
├── 特斯拉/
│   └── 特斯拉 -2026-03-08-1200.md
└── scripts/
    └── analysis_manager.py
"""

import os
import sys
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
import re


# 基础目录配置
# 优先使用环境变量 STOCK_ANALYSIS_WORKSPACE，否则使用默认路径
WORKSPACE_ROOT = Path(os.getenv('STOCK_ANALYSIS_WORKSPACE', Path(__file__).parent.parent))
STOCKS_DIR = WORKSPACE_ROOT / "stocks_analysis"
INTERMEDIATE_DIR = Path(os.getenv('STOCK_INTERMEDIATE_DIR', WORKSPACE_ROOT / "intermediate"))

# 数据类型配置
DATA_TYPES_CONFIG = {
    'analysis': {
        'base_dir': 'stocks_analysis',
        'identifier_type': 'name',  # 使用股票名称
        'file_prefix': '',  # 文件名前缀（空表示使用股票名称）
        'file_pattern': '{stock_name}-{timestamp}.md',
        'symlink_name': '最新分析.md',
        'needs_symlink': True,
        'subdirectory': None  # analysis 不需要子目录
    },
    'gf-summary': {
        'base_dir': 'intermediate',
        'identifier_type': 'intermediate',  # 中间数据类型
        'file_prefix': 'gf_summary_',
        'file_pattern': 'gf_summary_{date}.md',
        'symlink_name': '最新广发.md',
        'needs_symlink': True,
        'subdirectory': '广发证券数据'
    },
    'history-continuity': {
        'base_dir': 'intermediate',
        'identifier_type': 'intermediate',
        'file_prefix': 'continuity_',
        'file_pattern': 'continuity_{date}.md',
        'symlink_name': '最新连续性.md',
        'needs_symlink': True,
        'subdirectory': '历史连续性'
    },
    'deep-search': {
        'base_dir': 'intermediate',
        'identifier_type': 'intermediate',
        'file_prefix': 'search_',
        'file_pattern': 'search_{date}.md',
        'symlink_name': '最新搜索.md',
        'needs_symlink': True,
        'subdirectory': '深度搜索'
    }
}


def sanitize_stock_name(name: str) -> str:
    """清理股票名称，移除非法字符"""
    # 只保留中文、英文、数字和常见符号
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\-_]', '', name)
    return cleaned.strip() or "unknown_stock"


def get_stock_dir(stock_name: str) -> Path:
    """获取股票分析目录"""
    clean_name = sanitize_stock_name(stock_name)
    stock_dir = STOCKS_DIR / clean_name
    stock_dir.mkdir(parents=True, exist_ok=True)
    return stock_dir


def generate_filename(stock_name: str, timestamp: datetime = None) -> str:
    """生成分析文件名"""
    if timestamp is None:
        timestamp = datetime.now()
    time_str = timestamp.strftime("%Y-%m-%d-%H%M")
    clean_name = sanitize_stock_name(stock_name)
    return f"{clean_name}-{time_str}.md"


def generate_data_filename(
    stock_identifier: str,
    config: dict,
    timestamp: datetime,
    include_time: bool = False
) -> str:
    """
    根据数据类型配置生成文件名

    Args:
        stock_identifier: 股票标识
        config: 数据类型配置
        timestamp: 时间戳
        include_time: 是否包含时分秒（用于同日多次保存）

    Returns:
        文件名
    """
    if config['identifier_type'] == 'name':
        # 最终分析报告：{股票名称}-YYYY-MM-DD-HHMM.md
        stock_name = sanitize_stock_name(stock_identifier)
        time_str = timestamp.strftime("%Y-%m-%d-%H%M")
        return f"{stock_name}-{time_str}.md"

    else:  # intermediate (name-based)
        # 中间数据：{前缀}YYYY-MM-DD.md 或 {前缀}YYYY-MM-DD-HHmmss.md
        if include_time:
            time_str = timestamp.strftime("%Y-%m-%d-%H%M%S")
        else:
            time_str = timestamp.strftime("%Y-%m-%d")

        return f"{config['file_prefix']}{time_str}.md"


def create_latest_symlink(
    target_dir: Path,
    filepath: Path,
    symlink_name: str,
    subdirectory: str = None
):
    """
    创建或更新最新数据软链接

    Args:
        target_dir: 目标基础目录（股票名称目录）
        filepath: 实际文件路径
        symlink_name: 软链接名称
        subdirectory: 文件所在的子目录（如果有）
    """
    # 软链接始终放在基础目录下，而不是子目录下
    symlink_path = target_dir / symlink_name

    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()

    # 计算相对路径
    if subdirectory:
        # 文件在子目录下，软链接在父目录
        relative_path = Path(subdirectory) / filepath.name
    else:
        # 文件和软链接在同一目录
        relative_path = filepath.name

    symlink_path.symlink_to(relative_path)
    print(f"🔗 已更新软链接：{symlink_path} -> {relative_path}")


def save_analysis(stock_name: str, content: str, timestamp: datetime = None) -> Path:
    """
    保存股票分析结果

    Args:
        stock_name: 股票名称
        content: 分析内容 (Markdown 格式)
        timestamp: 时间戳 (默认当前时间)

    Returns:
        保存的文件路径
    """
    stock_dir = get_stock_dir(stock_name)
    filename = generate_filename(stock_name, timestamp)
    filepath = stock_dir / filename

    filepath.write_text(content, encoding='utf-8')
    print(f"✅ 分析已保存：{filepath}")

    # 创建/更新软链接指向最新分析
    create_latest_symlink(stock_dir, filepath, "最新分析.md")

    return filepath


def save_data(
    stock_identifier: str,
    data_type: str,
    content: str,
    timestamp: datetime = None
) -> Path:
    """
    统一的数据保存接口

    Args:
        stock_identifier: 股票标识（名称或代码）
        data_type: 数据类型 (analysis/gf-summary/history-continuity/deep-search)
        content: 数据内容
        timestamp: 时间戳（默认当前时间）

    Returns:
        保存的文件路径

    Raises:
        ValueError: 不支持的数据类型
    """
    if data_type not in DATA_TYPES_CONFIG:
        raise ValueError(f"不支持的数据类型: {data_type}。支持的类型: {list(DATA_TYPES_CONFIG.keys())}")

    config = DATA_TYPES_CONFIG[data_type]

    if timestamp is None:
        timestamp = datetime.now()

    # 确定目标目录
    if config['base_dir'] == 'stocks_analysis':
        target_dir = get_stock_dir(stock_identifier)  # 使用现有函数
    else:  # intermediate
        stock_name = sanitize_stock_name(stock_identifier)
        # 创建子目录结构：intermediate/股票名称/分类子目录/
        if config.get('subdirectory'):
            target_dir = INTERMEDIATE_DIR / stock_name / config['subdirectory']
        else:
            target_dir = INTERMEDIATE_DIR / stock_name
        target_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    filename = generate_data_filename(
        stock_identifier,
        config,
        timestamp
    )
    filepath = target_dir / filename

    # 处理同日多次保存（追加时间戳）
    if filepath.exists() and config['base_dir'] == 'intermediate':
        # 检查是否是同一天（只检查日期部分）
        date_str = timestamp.strftime("%Y-%m-%d")
        if date_str in filepath.name:
            # 追加时分秒
            filename = generate_data_filename(
                stock_identifier,
                config,
                timestamp,
                include_time=True
            )
            filepath = target_dir / filename

    # 保存文件
    filepath.write_text(content, encoding='utf-8')
    print(f"✅ {data_type} 数据已保存：{filepath}")

    # 创建软链接（软链接放在股票名称目录下，而不是子目录下）
    if config['needs_symlink']:
        # 股票名称的基础目录（用于放置软链接）
        stock_name = sanitize_stock_name(stock_identifier)
        if config['base_dir'] == 'stocks_analysis':
            symlink_base_dir = get_stock_dir(stock_identifier)
        else:
            symlink_base_dir = INTERMEDIATE_DIR / stock_name
            symlink_base_dir.mkdir(parents=True, exist_ok=True)

        create_latest_symlink(
            symlink_base_dir,
            filepath,
            config['symlink_name'],
            subdirectory=config.get('subdirectory')
        )

    return filepath


def list_data_files(
    stock_identifier: str,
    data_type: str = 'all',
    limit: int = 10
) -> dict:
    """
    列出股票的各类数据文件

    Args:
        stock_identifier: 股票标识（名称或代码）
        data_type: 数据类型 ('all' 或具体类型)
        limit: 每种类型最多返回的文件数

    Returns:
        {数据类型: [文件列表]} 字典
    """
    result = {}

    if data_type == 'all':
        types_to_check = list(DATA_TYPES_CONFIG.keys())
    else:
        types_to_check = [data_type]

    for dtype in types_to_check:
        config = DATA_TYPES_CONFIG[dtype]

        if config['base_dir'] == 'stocks_analysis':
            data_dir = get_stock_dir(stock_identifier)
        else:
            stock_name = sanitize_stock_name(stock_identifier)
            # 使用子目录结构
            if config.get('subdirectory'):
                data_dir = INTERMEDIATE_DIR / stock_name / config['subdirectory']
            else:
                data_dir = INTERMEDIATE_DIR / stock_name

        if not data_dir.exists():
            result[dtype] = []
            continue

        # 根据文件前缀过滤
        if config['file_prefix']:
            files = list(data_dir.glob(f"{config['file_prefix']}*.md"))
        else:
            # analysis 类型：使用股票名称作为前缀
            stock_name = sanitize_stock_name(stock_identifier)
            files = list(data_dir.glob(f"{stock_name}-*.md"))

        files = [f for f in files if not f.is_symlink()]  # 排除软链接
        files.sort(key=lambda x: x.name, reverse=True)

        result[dtype] = files[:limit]

    return result


def list_stocks() -> list:
    """列出所有已分析的股票"""
    if not STOCKS_DIR.exists():
        return []
    
    stocks = []
    for item in STOCKS_DIR.iterdir():
        if item.is_dir():
            stocks.append(item.name)
    
    return sorted(stocks)


def list_analyses(stock_name: str, limit: int = 10) -> list:
    """
    列出某股票的所有分析记录
    
    Args:
        stock_name: 股票名称
        limit: 最多返回的记录数
    
    Returns:
        分析文件列表 (按时间倒序)
    """
    stock_dir = get_stock_dir(stock_name)
    
    if not stock_dir.exists():
        return []
    
    files = list(stock_dir.glob("*.md"))
    # 按文件名排序 (包含时间戳)
    files.sort(key=lambda x: x.name, reverse=True)
    
    return files[:limit]


def get_recent_analysis(stock_name: str, days: int = 7) -> dict:
    """
    获取最近的分析结果
    
    Args:
        stock_name: 股票名称
        days: 最近多少天
    
    Returns:
        包含文件路径和内容的字典，如果没有则返回 None
    """
    stock_dir = get_stock_dir(stock_name)
    
    if not stock_dir.exists():
        return None
    
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    
    files = list(stock_dir.glob("*.md"))
    recent_files = [f for f in files if cutoff_str in f.name]
    
    if not recent_files:
        return None
    
    # 取最新的
    recent_files.sort(key=lambda x: x.name, reverse=True)
    latest_file = recent_files[0]
    
    return {
        'path': latest_file,
        'content': latest_file.read_text(encoding='utf-8'),
        'time': datetime.fromtimestamp(latest_file.stat().st_mtime)
    }


def read_analysis(stock_name: str, filename: str = None) -> str:
    """
    读取指定分析文件
    
    Args:
        stock_name: 股票名称
        filename: 文件名 (可选，默认读取最新的)
    
    Returns:
        文件内容
    """
    stock_dir = get_stock_dir(stock_name)
    
    if filename:
        filepath = stock_dir / filename
    else:
        # 读取最新的
        files = list_analyses(stock_name, limit=1)
        if not files:
            return f"❌ 未找到股票 '{stock_name}' 的分析记录"
        filepath = files[0]
    
    if not filepath.exists():
        return f"❌ 文件不存在：{filepath}"
    
    return filepath.read_text(encoding='utf-8')


def compare_analyses(stock_name: str, count: int = 2) -> str:
    """
    对比最近几次分析
    
    Args:
        stock_name: 股票名称
        count: 对比最近几次 (默认 2 次)
    
    Returns:
        对比报告
    """
    files = list_analyses(stock_name, limit=count)
    
    if len(files) < 2:
        return "⚠️ 分析记录不足 2 次，无法对比"
    
    report = f"# 📊 {stock_name} 分析对比报告\n\n"
    report += f"对比 {len(files)} 次分析记录\n\n"
    report += "---\n\n"
    
    for i, filepath in enumerate(reversed(files)):
        content = filepath.read_text(encoding='utf-8')
        # 提取时间
        match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{4})', filepath.name)
        time_str = match.group(1) if match else filepath.name
        
        report += f"## 分析 {i+1}: {time_str}\n\n"
        # 提取预测部分
        pred_match = re.search(r'(## 🎯 涨跌预测[\s\S]*?)(?=## |$)', content)
        if pred_match:
            report += pred_match.group(1) + "\n\n"
        report += "---\n\n"
    
    return report


# ==================== 持仓追踪功能 ====================

def get_holdings_file(stock_name: str) -> Path:
    """获取持仓文件路径"""
    stock_dir = get_stock_dir(stock_name)
    return stock_dir / "holdings.json"


def load_holdings(stock_name: str) -> dict:
    """加载持仓数据"""
    holdings_file = get_holdings_file(stock_name)
    
    if not holdings_file.exists():
        return {
            "stock_name": stock_name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "positions": [],  # 持仓记录列表
            "operations": []  # 操作历史
        }
    
    try:
        data = json.loads(holdings_file.read_text(encoding='utf-8'))
        return data
    except (json.JSONDecodeError, Exception) as e:
        print(f"⚠️ 读取持仓文件失败：{e}")
        return None


def save_holdings(stock_name: str, data: dict) -> Path:
    """保存持仓数据"""
    holdings_file = get_holdings_file(stock_name)
    data["updated_at"] = datetime.now().isoformat()
    
    holdings_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    return holdings_file


def set_position(stock_name: str, cost_price: float, quantity: int, note: str = "") -> Path:
    """
    设置持仓信息（初始建仓或调整持仓）
    
    Args:
        stock_name: 股票名称
        cost_price: 成本价格
        quantity: 数量
        note: 备注信息
    """
    holdings = load_holdings(stock_name)
    if holdings is None:
        holdings = {
            "stock_name": stock_name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "positions": [],
            "operations": []
        }
    
    # 添加持仓记录
    position_record = {
        "type": "position",
        "cost_price": cost_price,
        "quantity": quantity,
        "total_cost": cost_price * quantity,
        "timestamp": datetime.now().isoformat(),
        "note": note
    }
    holdings["positions"].append(position_record)
    
    # 添加操作记录
    operation_record = {
        "type": "set_position",
        "cost_price": cost_price,
        "quantity": quantity,
        "timestamp": datetime.now().isoformat(),
        "note": note
    }
    holdings["operations"].append(operation_record)
    
    filepath = save_holdings(stock_name, holdings)
    print(f"✅ 持仓已设置：{cost_price} × {quantity} 股")
    print(f"   总成本：¥{cost_price * quantity:,.2f}")
    print(f"   保存至：{filepath}")
    return filepath


def add_operation(stock_name: str, op_type: str, price: float, quantity: int, note: str = "") -> Path:
    """
    添加操作记录（买入/卖出）
    
    Args:
        stock_name: 股票名称
        op_type: 操作类型 (buy/sell)
        price: 成交价格
        quantity: 数量
        note: 备注信息
    """
    holdings = load_holdings(stock_name)
    if holdings is None:
        print("❌ 未找到持仓记录，请先使用 set 命令设置初始持仓")
        return None
    
    # 添加操作记录
    operation_record = {
        "type": op_type,
        "price": price,
        "quantity": quantity,
        "timestamp": datetime.now().isoformat(),
        "note": note
    }
    holdings["operations"].append(operation_record)
    
    # 更新持仓记录
    position_record = {
        "type": "position",
        "operation": op_type,
        "price": price,
        "quantity": quantity,
        "timestamp": datetime.now().isoformat(),
        "note": note
    }
    holdings["positions"].append(position_record)
    
    filepath = save_holdings(stock_name, holdings)
    
    op_name = "买入" if op_type == "buy" else "卖出"
    print(f"✅ {op_name}操作已记录：{price} × {quantity} 股")
    print(f"   保存至：{filepath}")
    return filepath


def show_holdings(stock_name: str, current_price: float = None) -> str:
    """
    显示持仓信息和盈亏
    
    Args:
        stock_name: 股票名称
        current_price: 当前价格（可选）
    """
    holdings = load_holdings(stock_name)
    
    if holdings is None or not holdings.get("positions"):
        return f"❌ 未找到 '{stock_name}' 的持仓记录"
    
    output = f"# 📊 {stock_name} 持仓追踪\n\n"
    
    # 计算当前持仓
    total_quantity = 0
    total_cost = 0.0
    
    for pos in holdings["positions"]:
        if pos.get("type") == "position":
            qty = pos.get("quantity", 0)
            # 只有建仓和买入才计入成本，卖出不计入成本
            if pos.get("operation") == "sell":
                # 卖出：只减少数量，不增加成本
                total_quantity -= qty
            else:
                # 建仓/买入：增加数量和成本
                cost = pos.get("cost_price", 0) or pos.get("price", 0)
                total_quantity += qty
                total_cost += cost * qty
    
    if total_quantity <= 0:
        output += "📭 当前无持仓\n\n"
    else:
        avg_cost = total_cost / total_quantity
        output += f"## 📈 当前持仓\n\n"
        output += f"- **持股数量**: {total_quantity} 股\n"
        output += f"- **平均成本**: ¥{avg_cost:.2f}\n"
        output += f"- **总成本**: ¥{total_cost:,.2f}\n"
        
        if current_price:
            market_value = total_quantity * current_price
            profit_loss = market_value - total_cost
            profit_loss_pct = (profit_loss / total_cost) * 100
            
            output += f"\n## 💰 盈亏情况\n\n"
            output += f"- **当前价格**: ¥{current_price:.2f}\n"
            output += f"- **市值**: ¥{market_value:,.2f}\n"
            
            if profit_loss >= 0:
                output += f"- **盈亏**: 📈 +¥{profit_loss:,.2f} (+{profit_loss_pct:.2f}%)\n"
            else:
                output += f"- **盈亏**: 📉 ¥{profit_loss:,.2f} ({profit_loss_pct:.2f}%)\n"
    
    # 操作历史
    if holdings.get("operations"):
        output += f"\n## 📝 操作历史\n\n"
        output += "| 时间 | 类型 | 价格 | 数量 | 备注 |\n"
        output += "|------|------|------|------|------|\n"
        
        for op in reversed(holdings["operations"][-10:]):  # 最近 10 条
            time_str = op.get("timestamp", "")[:16].replace("T", " ")
            op_type = op.get("type", "")
            if op_type == "set_position":
                type_name = "建仓"
            elif op_type == "buy":
                type_name = "买入"
            elif op_type == "sell":
                type_name = "卖出"
            else:
                type_name = op_type
            
            price = op.get("cost_price") or op.get("price", 0)
            qty = op.get("quantity", 0)
            note = op.get("note", "")
            
            output += f"| {time_str} | {type_name} | ¥{price:.2f} | {qty} | {note} |\n"
    
    return output


def main():
    parser = argparse.ArgumentParser(
        description='📊 股票分析报告管理器 - 保存、读取和管理股票分析结果',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
快速示例:
  python analysis_manager.py save "赛力斯" --stdin
  python analysis_manager.py list
  python analysis_manager.py history "赛力斯"
  python analysis_manager.py read "赛力斯"
  python analysis_manager.py compare "赛力斯" --count 2

持仓追踪:
  python analysis_manager.py set "赛力斯" --price 52.5 --qty 1000 --note "初始建仓"
  python analysis_manager.py buy "赛力斯" --price 55.0 --qty 500 --note "加仓"
  python analysis_manager.py sell "赛力斯" --price 58.0 --qty 300 --note "部分止盈"
  python analysis_manager.py holdings "赛力斯" --current-price 56.8

📖 完整文档：ANALYSIS_TOOL.md
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # save 命令
    save_parser = subparsers.add_parser('save', help='保存分析结果')
    save_parser.add_argument('stock_name', help='股票名称')
    save_parser.add_argument('--content', '-c', help='分析内容')
    save_parser.add_argument('--stdin', action='store_true', help='从 stdin 读取内容')
    save_parser.add_argument('--file', '-f', help='从文件读取内容')
    
    # list 命令
    subparsers.add_parser('list', help='列出所有已分析的股票')
    
    # history 命令
    history_parser = subparsers.add_parser('history', help='列出股票分析历史')
    history_parser.add_argument('stock_name', help='股票名称')
    history_parser.add_argument('--limit', '-l', type=int, default=10, help='最多显示的记录数')
    
    # read 命令
    read_parser = subparsers.add_parser('read', help='读取分析结果')
    read_parser.add_argument('stock_name', help='股票名称')
    read_parser.add_argument('--file', '-f', help='文件名 (默认读取最新)')
    
    # recent 命令
    recent_parser = subparsers.add_parser('recent', help='获取最近的分析')
    recent_parser.add_argument('stock_name', help='股票名称')
    recent_parser.add_argument('--days', '-d', type=int, default=7, help='最近多少天')
    
    # compare 命令
    compare_parser = subparsers.add_parser('compare', help='对比多次分析')
    compare_parser.add_argument('stock_name', help='股票名称')
    compare_parser.add_argument('--count', '-c', type=int, default=2, help='对比最近几次')
    
    # 持仓追踪命令
    # set 命令 - 设置初始持仓
    set_parser = subparsers.add_parser('set', help='设置持仓（成本价 + 数量）')
    set_parser.add_argument('stock_name', help='股票名称')
    set_parser.add_argument('--price', '-p', type=float, required=True, help='成本价格')
    set_parser.add_argument('--qty', '-q', type=int, required=True, help='持股数量')
    set_parser.add_argument('--note', '-n', type=str, default='', help='备注信息')
    
    # buy 命令 - 买入
    buy_parser = subparsers.add_parser('buy', help='记录买入操作')
    buy_parser.add_argument('stock_name', help='股票名称')
    buy_parser.add_argument('--price', '-p', type=float, required=True, help='买入价格')
    buy_parser.add_argument('--qty', '-q', type=int, required=True, help='买入数量')
    buy_parser.add_argument('--note', '-n', type=str, default='', help='备注信息')
    
    # sell 命令 - 卖出
    sell_parser = subparsers.add_parser('sell', help='记录卖出操作')
    sell_parser.add_argument('stock_name', help='股票名称')
    sell_parser.add_argument('--price', '-p', type=float, required=True, help='卖出价格')
    sell_parser.add_argument('--qty', '-q', type=int, required=True, help='卖出数量')
    sell_parser.add_argument('--note', '-n', type=str, default='', help='备注信息')
    
    # holdings 命令 - 查看持仓
    holdings_parser = subparsers.add_parser('holdings', help='查看持仓和盈亏')
    holdings_parser.add_argument('stock_name', help='股票名称')
    holdings_parser.add_argument('--current-price', '-c', type=float, help='当前价格（用于计算盈亏）')

    # save-data 命令 - 统一数据保存接口
    save_data_parser = subparsers.add_parser('save-data', help='保存各类数据')
    save_data_parser.add_argument('stock_identifier', help='股票名称或代码')
    save_data_parser.add_argument('--type', '-t', required=True,
                                   choices=['analysis', 'gf-summary', 'history-continuity', 'deep-search'],
                                   help='数据类型')
    save_data_parser.add_argument('--content', '-c', help='数据内容')
    save_data_parser.add_argument('--stdin', action='store_true', help='从 stdin 读取内容')
    save_data_parser.add_argument('--file', '-f', help='从文件读取内容')

    # list-data 命令 - 列出各类数据文件
    list_data_parser = subparsers.add_parser('list-data', help='列出各类数据文件')
    list_data_parser.add_argument('stock_identifier', help='股票名称或代码')
    list_data_parser.add_argument('--type', '-t', default='all',
                                   choices=['all', 'analysis', 'gf-summary', 'history-continuity', 'deep-search'],
                                   help='数据类型 (默认: all)')
    list_data_parser.add_argument('--limit', '-l', type=int, default=10, help='每种类型最多显示的文件数')
    
    args = parser.parse_args()
    
    if args.command == 'save':
        content = None
        if args.content:
            content = args.content
        elif args.stdin:
            content = sys.stdin.read()
        elif args.file:
            content = Path(args.file).read_text(encoding='utf-8')
        else:
            print("❌ 错误：请提供 --content、--stdin 或 --file")
            sys.exit(1)
        
        save_analysis(args.stock_name, content)
    
    elif args.command == 'list':
        stocks = list_stocks()
        if not stocks:
            print("📭 暂无分析记录")
        else:
            print(f"📊 已分析的股票 ({len(stocks)} 只):")
            for stock in stocks:
                analyses = list_analyses(stock, limit=999)
                print(f"  • {stock} - {len(analyses)} 次分析")
    
    elif args.command == 'history':
        files = list_analyses(args.stock_name, args.limit)
        if not files:
            print(f"📭 未找到 '{args.stock_name}' 的分析记录")
        else:
            print(f"📈 {args.stock_name} 分析历史:")
            for f in files:
                print(f"  • {f.resolve()}")
    
    elif args.command == 'read':
        content = read_analysis(args.stock_name, args.file)
        print(content)
    
    elif args.command == 'recent':
        result = get_recent_analysis(args.stock_name, args.days)
        if result:
            print(f"📄 最新分析 ({result['time'].strftime('%Y-%m-%d %H:%M')}):")
            print(result['content'])
        else:
            print(f"📭 {args.days} 天内无分析记录")
    
    elif args.command == 'compare':
        report = compare_analyses(args.stock_name, args.count)
        print(report)
    
    # 持仓追踪命令
    elif args.command == 'set':
        set_position(args.stock_name, args.price, args.qty, args.note)
    
    elif args.command == 'buy':
        add_operation(args.stock_name, 'buy', args.price, args.qty, args.note)
    
    elif args.command == 'sell':
        add_operation(args.stock_name, 'sell', args.price, args.qty, args.note)
    
    elif args.command == 'holdings':
        report = show_holdings(args.stock_name, args.current_price)
        print(report)

    elif args.command == 'save-data':
        # 获取内容
        content = None
        if args.content:
            content = args.content
        elif args.stdin:
            content = sys.stdin.read()
        elif args.file:
            content = Path(args.file).read_text(encoding='utf-8')
        else:
            print("❌ 错误：请提供 --content、--stdin 或 --file")
            sys.exit(1)

        # 调用统一保存接口
        try:
            filepath = save_data(args.stock_identifier, args.type, content)
            print(f"\n✓ 数据已保存到: {filepath}")
        except ValueError as e:
            print(f"❌ 错误：{e}")
            sys.exit(1)

    elif args.command == 'list-data':
        # 查询数据文件
        files_dict = list_data_files(args.stock_identifier, args.type, args.limit)

        for dtype, files in files_dict.items():
            if not files:
                print(f"\n📭 {dtype}: 暂无数据")
            else:
                print(f"\n📊 {dtype} ({len(files)} 个文件):")
                for f in files:
                    # 获取文件大小
                    size_kb = f.stat().st_size / 1024
                    print(f"  • {f.resolve()} ({size_kb:.1f} KB)")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
