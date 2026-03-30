#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟盘交易系统
支持 A股和港股的模拟交易，管理独立资金池
"""

import os
import sys
import json
import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import requests

# 基础目录配置（与 analysis_manager.py 保持一致）
SKILL_ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = Path(os.getenv('STOCK_ANALYSIS_WORKSPACE', SKILL_ROOT))
INTERMEDIATE_DIR = Path(os.getenv('STOCK_INTERMEDIATE_DIR', WORKSPACE_ROOT / "intermediate"))


class StockPriceFetcher:
    """股票价格数据抓取器（简化版，仅支持 A股/港股）"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def fetch_tencent_stock_data(self, stock_codes: List[str]) -> Dict[str, dict]:
        """
        从腾讯获取股票实时数据
        支持港股（HK）、深沪股票

        Args:
            stock_codes: 股票代码列表，如 ['hk00700', 'sz000001', 'sh600000']

        Returns:
            股票数据字典
        """
        results = {}
        hk_sh_sz = [c.lower() for c in stock_codes if c.lower().startswith(('hk', 'sh', 'sz'))]
        if not hk_sh_sz:
            return results

        # 转换代码格式
        formatted_codes = []
        for code in hk_sh_sz:
            code_lower = code.lower()
            if code_lower.startswith('hk'):
                formatted_codes.append('r_' + code_lower)
            else:
                formatted_codes.append(code_lower)

        codes_str = ','.join(formatted_codes)
        url = f"http://qt.gtimg.cn/?_={int(datetime.now().timestamp())}&q={codes_str}"

        try:
            headers = {
                'Host': 'qt.gtimg.cn',
                'Referer': 'https://gu.qq.com/',
                **self.headers
            }
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.encoding = 'gb18030'

            for line in response.text.strip().split('\n'):
                if not line or '~' not in line:
                    continue

                info = self._parse_tencent_data(line)
                if info and info['code'] in stock_codes:
                    results[info['code']] = info

            return results

        except Exception as e:
            print(f"Error fetching from Tencent: {e}")
            return {}

    def get_realtime_price(self, stock_code: str) -> Optional[dict]:
        """
        获取单只股票的实时价格

        Args:
            stock_code: 股票代码（支持A股、港股）

        Returns:
            股票信息字典，如果失败返回None
        """
        result = self.fetch_tencent_stock_data([stock_code])
        return result.get(stock_code)

    def _parse_tencent_data(self, line: str) -> Optional[dict]:
        """
        解析腾讯返回的股票数据

        Args:
            line: 数据行

        Returns:
            解析后的股票信息
        """
        if '=' not in line:
            return None

        try:
            var_name, data = line.split('=', 1)
            code = var_name.replace('v_r_', '').replace('v_', '')

            parts = data.strip('"').strip(';').split('~')
            if len(parts) < 35:
                return None

            return {
                'code': code.lower(),
                'name': parts[1],
                'price': float(parts[3]) if parts[3] else None,
                'pre_close': float(parts[4]) if parts[4] else None,
                'open': float(parts[5]) if parts[5] else None,
                'high': float(parts[33]) if len(parts) > 33 else None,
                'low': float(parts[34]) if len(parts) > 34 else None,
                'volume': parts[6],
                'date': parts[29].replace('/', '-') if len(parts) > 29 else '',
                'time': ':'.join(parts[30].split(':')[:3]) if len(parts) > 30 else '',
                'source': 'tencent'
            }

        except (ValueError, IndexError) as e:
            print(f"Error parsing Tencent data: {e}")
            return None


class StockCodeSearcher:
    """股票代码查询器（简化版，仅支持 A股）"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }

    def search_cn_stocks(self, keyword: str, limit: int = 20) -> List[dict]:
        """
        搜索A股股票代码

        Args:
            keyword: 搜索关键词（股票名称或代码）
            limit: 返回结果数量

        Returns:
            股票列表
        """
        url = f"https://suggest3.sinajs.cn/suggest/type=11&key={keyword}&name=suggestdata"

        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            content = response.text

            if 'suggestdata="' in content:
                data_str = content.split('suggestdata="')[1].split('";')[0]

                results = []
                for item in data_str.split(';'):
                    if not item:
                        continue

                    parts = item.split(',')
                    if len(parts) >= 5:
                        name = parts[0]
                        code = parts[2]
                        full_code = parts[3]

                        if full_code:
                            formatted_code = full_code.lower()
                        elif len(code) == 6:
                            if code.startswith('6'):
                                formatted_code = f'sh{code}'
                            elif code.startswith(('0', '3')):
                                formatted_code = f'sz{code}'
                            else:
                                formatted_code = code
                        else:
                            formatted_code = code

                        results.append({
                            'name': name,
                            'code': formatted_code,
                            'original_code': code,
                            'full_code': full_code,
                            'market': 'A股',
                            'source': '新浪财经'
                        })

                return results[:limit]

            return []

        except Exception as e:
            print(f"Error searching stocks: {e}")
            return []
INTERMEDIATE_DIR = Path(os.getenv('STOCK_INTERMEDIATE_DIR', WORKSPACE_ROOT / "intermediate"))


def sanitize_stock_name(name: str) -> str:
    """清理股票名称，移除非法字符"""
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\-_]', '', name)
    return cleaned.strip() or "unknown_stock"


def get_trading_dir(stock_name: str) -> Path:
    """获取模拟买卖目录"""
    clean_name = sanitize_stock_name(stock_name)
    trading_dir = INTERMEDIATE_DIR / clean_name / "模拟买卖"
    trading_dir.mkdir(parents=True, exist_ok=True)
    return trading_dir


def get_holdings_file(stock_name: str) -> Path:
    """获取 holdings.json 路径"""
    return get_trading_dir(stock_name) / "holdings.json"


def get_operations_file(stock_name: str) -> Path:
    """获取 operations.json 路径"""
    return get_trading_dir(stock_name) / "operations.json"


def load_holdings(stock_name: str) -> Optional[dict]:
    """加载持仓数据"""
    holdings_file = get_holdings_file(stock_name)

    if not holdings_file.exists():
        return None

    try:
        data = json.loads(holdings_file.read_text(encoding='utf-8'))
        return data
    except json.JSONDecodeError as e:
        print(f"❌ 读取持仓文件失败：{e}")
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


def load_operations(stock_name: str) -> Optional[dict]:
    """加载操作记录数据"""
    operations_file = get_operations_file(stock_name)

    if not operations_file.exists():
        return None

    try:
        data = json.loads(operations_file.read_text(encoding='utf-8'))
        return data
    except json.JSONDecodeError as e:
        print(f"❌ 读取操作记录文件失败：{e}")
        return None


def save_operations(stock_name: str, data: dict) -> Path:
    """保存操作记录数据"""
    operations_file = get_operations_file(stock_name)
    data["updated_at"] = datetime.now().isoformat()

    operations_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    return operations_file


def add_operation(stock_name: str, op_record: dict) -> Path:
    """添加操作记录到 operations.json"""
    ops_data = load_operations(stock_name)

    if ops_data is None:
        # 首次创建
        ops_data = {
            "stock_name": stock_name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "operations": []
        }

    ops_data["operations"].append(op_record)
    return save_operations(stock_name, ops_data)


def get_stock_code(stock_name: str) -> Optional[str]:
    """
    获取股票代码
    支持 A股和港股，不支持美股
    """
    searcher = StockCodeSearcher()

    # 搜索 A股
    results = searcher.search_cn_stocks(stock_name, limit=3)
    if results:
        for result in results:
            market = result.get('market', '')
            code = result.get('code', '')
            # 只使用 A股，跳过港股和美股
            if market == 'A股' and code and code.lower().startswith(('sh', 'sz', 'bj')):
                print(f"✅ 找到股票代码：{result['name']} ({code})")
                return code.lower()

    print(f"⚠️  未找到股票 '{stock_name}' 的 A股代码")
    return None


def get_realtime_price(stock_code: str) -> Optional[dict]:
    """
    获取实时股价
    只支持 A股和港股，主动跳过美股
    """
    fetcher = StockPriceFetcher()
    code_lower = stock_code.lower()

    # 跳过美股代码
    if code_lower.startswith(('gb_', 'us')):
        print("⚠️  不支持美股代码")
        return None

    # A股和港股
    result = fetcher.get_realtime_price(stock_code)
    if result:
        print(f"📈 实时价格：¥{result.get('price', 0):.2f}")
    return result


def init_capital_pool(stock_name: str, capital: float, stock_code: str = None, force: bool = False) -> Path:
    """
    初始化资金池

    Args:
        stock_name: 股票名称
        capital: 初始资金
        stock_code: 股票代码（可选，会自动查询）
        force: 是否强制删除现有资金池
    """
    clean_name = sanitize_stock_name(stock_name)

    # 检查是否已存在
    existing = load_holdings(stock_name)
    if existing:
        if force:
            print(f"⚠️  股票 '{clean_name}' 的资金池已存在，强制删除并重新初始化")
            # 删除现有文件
            holdings_file = get_holdings_file(stock_name)
            operations_file = get_operations_file(stock_name)
            if holdings_file.exists():
                holdings_file.unlink()
            if operations_file.exists():
                operations_file.unlink()
        else:
            print(f"⚠️  股票 '{clean_name}' 的资金池已存在，现有资金：¥{existing['capital_pool']['total']:,.2f}")
            print("   如需重新初始化，请先删除相关文件或使用 --force 参数")
            print(f"   文件位置：{get_holdings_file(stock_name)}")
            return None

    # 获取股票代码
    if stock_code is None:
        stock_code = get_stock_code(stock_name)

    # 创建数据结构
    now = datetime.now().isoformat()

    holdings_data = {
        "stock_name": clean_name,
        "stock_code": stock_code,
        "created_at": now,
        "updated_at": now,
        "capital_pool": {
            "total": capital,
            "available": capital,
            "used": 0.0
        },
        "positions": []
    }

    # 保存持仓数据
    filepath = save_holdings(stock_name, holdings_data)

    # 记录初始化操作
    add_operation(stock_name, {
        "type": "init",
        "capital": capital,
        "timestamp": now
    })

    print(f"✅ 资金池初始化成功：{clean_name}")
    print(f"   初始资金：¥{capital:,.2f}")
    print(f"   股票代码：{stock_code or '未知'}")
    print(f"   保存位置：{filepath}")

    return filepath


def show_capital_pool(stock_name: str) -> str:
    """
    显示资金池状态

    Args:
        stock_name: 股票名称

    Returns:
        格式化的资金池信息
    """
    clean_name = sanitize_stock_name(stock_name)
    holdings = load_holdings(stock_name)

    if not holdings:
        return f"❌ 未找到股票 '{clean_name}' 的资金池记录"

    pool = holdings['capital_pool']

    output = f"# 💰 {clean_name} 资金池状态\n\n"
    output += f"- **总资金**: ¥{pool['total']:,.2f}\n"
    output += f"- **可用资金**: ¥{pool['available']:,.2f}\n"
    output += f"- **占用资金**: ¥{pool['used']:,.2f}\n"

    if pool['total'] > 0:
        usage_rate = (pool['used'] / pool['total']) * 100
        output += f"- **资金使用率**: {usage_rate:.1f}%\n"

    # 计算当前持仓市值
    total_quantity = 0
    total_cost = 0.0

    for pos in holdings['positions']:
        if pos.get('operation') == 'sell':
            continue
        qty = pos.get('quantity', 0)
        total_quantity += qty
        total_cost += pos.get('total_cost', 0)

    if total_quantity > 0:
        output += f"\n## 📊 持仓概览\n\n"
        output += f"- **持股数量**: {total_quantity} 股\n"
        output += f"- **持仓成本**: ¥{total_cost:,.2f}\n"
        if total_quantity > 0:
            avg_cost = total_cost / total_quantity
            output += f"- **平均成本**: ¥{avg_cost:.2f}/股\n"

        # 尝试获取实时价格
        stock_code = holdings.get('stock_code')
        if stock_code:
            price_data = get_realtime_price(stock_code)
            if price_data:
                current_price = price_data.get('price')
                if current_price:
                    market_value = total_quantity * current_price
                    profit_loss = market_value - total_cost
                    profit_loss_pct = (profit_loss / total_cost) * 100

                    output += f"- **当前价格**: ¥{current_price:.2f}/股\n"
                    output += f"- **持仓市值**: ¥{market_value:,.2f}\n"
                    if profit_loss >= 0:
                        output += f"- **浮动盈亏**: 📈 +¥{profit_loss:,.2f} (+{profit_loss_pct:.2f}%)\n"
                    else:
                        output += f"- **浮动盈亏**: 📉 ¥{profit_loss:,.2f} ({profit_loss_pct:.2f}%)\n"

    return output


def buy_stock(stock_name: str, qty: int = None, amount: float = None, note: str = "") -> Optional[Path]:
    """
    买入股票

    Args:
        stock_name: 股票名称
        qty: 买入股数（与 amount 二选一）
        amount: 买入金额（与 qty 二选一）
        note: 备注信息

    Returns:
        保存的文件路径，失败返回 None
    """
    clean_name = sanitize_stock_name(stock_name)
    holdings = load_holdings(stock_name)

    if not holdings:
        print(f"❌ 未找到股票 '{clean_name}' 的资金池，请先使用 init 命令初始化")
        return None

    # 获取股票代码
    stock_code = holdings.get('stock_code')
    if not stock_code:
        print(f"⚠️  未找到股票代码，尝试重新获取...")
        stock_code = get_stock_code(stock_name)
        if not stock_code:
            return None
        holdings['stock_code'] = stock_code
        save_holdings(stock_name, holdings)

    # 获取实时价格
    price_data = get_realtime_price(stock_code)
    if not price_data:
        return None

    current_price = price_data.get('price')
    if not current_price:
        print("❌ 无法获取实时价格")
        return None

    # 计算交易数量和金额
    if qty is not None:
        # 按股数买入
        trade_qty = qty
        trade_amount = trade_qty * current_price
    elif amount is not None:
        # 按金额买入
        trade_amount = amount
        trade_qty = int(trade_amount / current_price)
        if trade_qty == 0:
            print(f"❌ 金额 ¥{amount:,.2f} 不足以买入 1 股")
            return None
    else:
        print("❌ 请指定 --qty 或 --amount 参数")
        return None

    # 检查资金是否充足
    available = holdings['capital_pool']['available']
    required = trade_qty * current_price

    if required > available:
        shortage = required - available
        print(f"❌ 资金不足")
        print(f"   需要：¥{required:,.2f}")
        print(f"   可用：¥{available:,.2f}")
        print(f"   缺口：¥{shortage:,.2f}")
        return None

    # 更新资金池
    holdings['capital_pool']['available'] -= required
    holdings['capital_pool']['used'] += required

    # 添加持仓记录
    position_record = {
        "operation": "buy",
        "price": current_price,
        "quantity": trade_qty,
        "total_cost": required,
        "timestamp": datetime.now().isoformat(),
        "note": note
    }
    holdings['positions'].append(position_record)

    # 保存持仓数据
    save_holdings(stock_name, holdings)

    # 添加操作记录
    add_operation(stock_name, {
        "type": "buy",
        "price": current_price,
        "quantity": trade_qty,
        "amount": required,
        "timestamp": datetime.now().isoformat(),
        "note": note
    })

    print(f"✅ 买入成功：{clean_name}")
    print(f"   价格：¥{current_price:.2f} × {trade_qty} 股")
    print(f"   金额：¥{required:,.2f}")
    print(f"   剩余可用：¥{holdings['capital_pool']['available']:,.2f}")

    return get_holdings_file(stock_name)


def sell_stock(stock_name: str, qty: int = None, all_shares: bool = False, note: str = "") -> Optional[Path]:
    """
    卖出股票

    Args:
        stock_name: 股票名称
        qty: 卖出股数
        all_shares: 是否全部卖出
        note: 备注信息

    Returns:
        保存的文件路径，失败返回 None
    """
    clean_name = sanitize_stock_name(stock_name)
    holdings = load_holdings(stock_name)

    if not holdings:
        print(f"❌ 未找到股票 '{clean_name}' 的持仓记录")
        return None

    # 计算当前持仓
    total_quantity = 0
    for pos in holdings['positions']:
        if pos.get('operation') != 'sell':
            total_quantity += pos.get('quantity', 0)

    if total_quantity == 0:
        print(f"📭 当前无持仓")
        return None

    # 确定卖出数量
    if all_shares:
        trade_qty = total_quantity
    elif qty is not None:
        trade_qty = qty
    else:
        print("❌ 请指定 --qty 或 --all 参数")
        return None

    if trade_qty > total_quantity:
        print(f"❌ 持仓不足")
        print(f"   想卖：{trade_qty} 股")
        print(f"   持仓：{total_quantity} 股")
        return None

    # 获取股票代码和实时价格
    stock_code = holdings.get('stock_code')
    if not stock_code:
        print("❌ 未找到股票代码")
        return None

    price_data = get_realtime_price(stock_code)
    if not price_data:
        return None

    current_price = price_data.get('price')
    if not current_price:
        print("❌ 无法获取实时价格")
        return None

    # 计算成交金额
    trade_amount = trade_qty * current_price

    # 计算成本（先进先出）
    cost_amount = 0.0
    remaining_qty = trade_qty

    for pos in holdings['positions']:
        if remaining_qty <= 0:
            break
        if pos.get('operation') == 'sell':
            continue

        pos_qty = pos.get('quantity', 0)
        cost_per_share = pos.get('total_cost', 0) / pos_qty if pos_qty > 0 else 0

        if pos_qty <= remaining_qty:
            cost_amount += pos.get('total_cost', 0)
            remaining_qty -= pos_qty
        else:
            cost_amount += remaining_qty * cost_per_share
            remaining_qty = 0

    profit = trade_amount - cost_amount

    # 更新资金池
    holdings['capital_pool']['available'] += trade_amount
    holdings['capital_pool']['used'] -= cost_amount

    # 添加卖出仓位记录（用于成本计算）
    sell_position_record = {
        "operation": "sell",
        "price": current_price,
        "quantity": trade_qty,
        "total_cost": cost_amount,
        "timestamp": datetime.now().isoformat(),
        "note": note
    }
    holdings['positions'].append(sell_position_record)

    # 保存持仓数据
    save_holdings(stock_name, holdings)

    # 添加操作记录
    add_operation(stock_name, {
        "type": "sell",
        "price": current_price,
        "quantity": trade_qty,
        "amount": trade_amount,
        "cost": cost_amount,
        "profit": profit,
        "timestamp": datetime.now().isoformat(),
        "note": note
    })

    op_name = "全部卖出" if all_shares else "卖出"
    print(f"✅ {op_name}成功：{clean_name}")
    print(f"   价格：¥{current_price:.2f} × {trade_qty} 股")
    print(f"   成交金额：¥{trade_amount:,.2f}")
    print(f"   成本：¥{cost_amount:,.2f}")
    if profit >= 0:
        print(f"   盈利：📈 +¥{profit:,.2f}")
    else:
        print(f"   亏损：📉 ¥{profit:,.2f}")
    print(f"   可用资金：¥{holdings['capital_pool']['available']:,.2f}")

    return get_holdings_file(stock_name)


def show_holdings(stock_name: str) -> str:
    """
    显示持仓报告

    Args:
        stock_name: 股票名称

    Returns:
        格式化的持仓报告
    """
    clean_name = sanitize_stock_name(stock_name)
    holdings = load_holdings(stock_name)

    if not holdings:
        return f"❌ 未找到股票 '{clean_name}' 的持仓记录"

    output = f"# 📊 {clean_name} 持仓报告\n\n"

    # 数据结构
    pool = holdings['capital_pool']
    output += f"## 💰 资金池\n\n"
    output += f"- **总资金**: ¥{pool['total']:,.2f}\n"
    output += f"- **可用资金**: ¥{pool['available']:,.2f}\n"
    output += f"- **占用资金**: ¥{pool['used']:,.2f}\n"
    output += f"- **资金使用率**: {(pool['used']/pool['total']*100):.1f}%\n\n"

    # 计算持仓
    total_quantity = 0
    total_cost = 0.0

    for pos in holdings['positions']:
        if pos.get('operation') == 'sell':
            continue
        qty = pos.get('quantity', 0)
        total_quantity += qty
        total_cost += pos.get('total_cost', 0)

    if total_quantity == 0:
        output += "## 📭 当前无持仓\n"
        return output

    avg_cost = total_cost / total_quantity

    output += f"## 📈 当前持仓\n\n"
    output += f"- **持股数量**: {total_quantity} 股\n"
    output += f"- **平均成本**: ¥{avg_cost:.2f}\n"
    output += f"- **持仓成本**: ¥{total_cost:,.2f}\n"

    # 获取实时价格
    stock_code = holdings.get('stock_code')
    if stock_code:
        price_data = get_realtime_price(stock_code)
        if price_data and price_data.get('price'):
            current_price = price_data.get('price')
            market_value = total_quantity * current_price
            profit_loss = market_value - total_cost
            profit_loss_pct = (profit_loss / total_cost) * 100

            output += f"- **当前价格**: ¥{current_price:.2f}\n"
            output += f"- **持仓市值**: ¥{market_value:,.2f}\n"
            if profit_loss >= 0:
                output += f"- **浮动盈亏**: 📈 +¥{profit_loss:,.2f} (+{profit_loss_pct:.2f}%)\n"
            else:
                output += f"- **浮动盈亏**: 📉 ¥{profit_loss:,.2f} ({profit_loss_pct:.2f}%)\n"

    # 持仓明细
    output += f"\n## 📋 持仓明细\n\n"
    output += "| 时间 | 操作 | 价格 | 数量 | 成本 | 备注 |\n"
    output += "|------|------|------|------|------|------|\n"

    for pos in reversed(holdings['positions']):
        if pos.get('operation') == 'sell':
            continue

        time_str = pos.get('timestamp', '')[:16].replace('T', ' ')
        op_type = "买入"
        price = pos.get('price', 0)
        qty = pos.get('quantity', 0)
        cost = pos.get('total_cost', 0)
        note = pos.get('note', '')

        output += f"| {time_str} | {op_type} | ¥{price:.2f} | {qty} | ¥{cost:,.2f} | {note} |\n"

    return output


def show_operations(stock_name: str) -> str:
    """
    显示操作历史

    Args:
        stock_name: 股票名称

    Returns:
        格式化的操作历史
    """
    clean_name = sanitize_stock_name(stock_name)
    ops_data = load_operations(stock_name)

    if not ops_data:
        return f"❌ 未找到股票 '{clean_name}' 的操作记录"

    output = f"# 📝 {clean_name} 操作历史\n\n"

    operations = ops_data.get('operations', [])

    if not operations:
        output += "📭 暂无操作记录\n"
        return output

    # 统计
    total_buy = 0
    total_sell = 0
    total_profit = 0.0
    sell_count = 0

    for op in operations:
        if op.get('type') == 'buy':
            total_buy += op.get('amount', 0)
        elif op.get('type') == 'sell':
            total_sell += op.get('amount', 0)
            total_profit += op.get('profit', 0)
            sell_count += 1

    output += f"## 📊 操作统计\n\n"
    output += f"- **总买入金额**: ¥{total_buy:,.2f}\n"
    output += f"- **总卖出金额**: ¥{total_sell:,.2f}\n"
    if sell_count > 0:
        output += f"- **累计盈亏**: "
        if total_profit >= 0:
            output += f"📈 +¥{total_profit:,.2f}\n"
        else:
            output += f"📉 ¥{total_profit:,.2f}\n"
        output += f"- **交易次数**: {sell_count} 次\n\n"

    # 操作明细
    output += f"## 📋 操作流水\n\n"
    output += "| 时间 | 类型 | 价格 | 数量 | 金额 | 盈亏 | 备注 |\n"
    output += "|------|------|------|------|------|------|------|\n"

    for op in reversed(operations):
        time_str = op.get('timestamp', '')[:16].replace('T', ' ')
        op_type = op.get('type', '')

        if op_type == 'init':
            output += f"| {time_str} | 初始化 | - | - | ¥{op.get('capital', 0):,.2f} | - | 初始资金池 |\n"
        elif op_type == 'buy':
            output += f"| {time_str} | 买入 | ¥{op.get('price', 0):.2f} | {op.get('quantity', 0)} | ¥{op.get('amount', 0):,.2f} | - | {op.get('note', '')} |\n"
        elif op_type == 'sell':
            profit = op.get('profit', 0)
            profit_str = f"+¥{profit:,.2f}" if profit >= 0 else f"¥{profit:,.2f}"
            output += f"| {time_str} | 卖出 | ¥{op.get('price', 0):.2f} | {op.get('quantity', 0)} | ¥{op.get('amount', 0):,.2f} | {profit_str} | {op.get('note', '')} |\n"

    return output


def show_profit(stock_name: str) -> str:
    """
    显示收益报告

    Args:
        stock_name: 股票名称

    Returns:
        格式化的收益报告
    """
    clean_name = sanitize_stock_name(stock_name)
    holdings = load_holdings(stock_name)
    ops_data = load_operations(stock_name)

    if not holdings or not ops_data:
        return f"❌ 未找到股票 '{clean_name}' 的数据"

    output = f"# 💰 {clean_name} 收益报告\n\n"

    operations = ops_data.get('operations', [])
    pool = holdings['capital_pool']

    # 统计卖出收益
    total_sell_amount = 0.0
    total_sell_cost = 0.0
    realized_profit = 0.0
    sell_operations = []

    for op in operations:
        if op.get('type') == 'sell':
            amount = op.get('amount', 0)
            cost = op.get('cost', 0)
            profit = op.get('profit', 0)

            total_sell_amount += amount
            total_sell_cost += cost
            realized_profit += profit
            sell_operations.append(op)

    # 计算当前持仓浮动盈亏
    total_quantity = 0
    total_cost = 0.0

    for pos in holdings['positions']:
        if pos.get('operation') == 'sell':
            continue
        qty = pos.get('quantity', 0)
        total_quantity += qty
        total_cost += pos.get('total_cost', 0)

    floating_profit = 0.0

    if total_quantity > 0:
        stock_code = holdings.get('stock_code')
        if stock_code:
            price_data = get_realtime_price(stock_code)
            if price_data and price_data.get('price'):
                current_price = price_data.get('price')
                market_value = total_quantity * current_price
                floating_profit = market_value - total_cost

    # 总盈亏
    total_profit = realized_profit + floating_profit

    output += f"## 📊 总体收益\n\n"
    output += f"- **实现盈亏（已平仓）**: "
    if realized_profit >= 0:
        output += f"📈 +¥{realized_profit:,.2f}\n"
    else:
        output += f"📉 ¥{realized_profit:,.2f}\n"

    output += f"- **浮动盈亏（持仓中）**: "
    if floating_profit >= 0:
        output += f"📈 +¥{floating_profit:,.2f}\n"
    else:
        output += f"📉 ¥{floating_profit:,.2f}\n"

    output += f"- **总盈亏**: "
    if total_profit >= 0:
        output += f"📈 +¥{total_profit:,.2f}\n"
    else:
        output += f"📉 ¥{total_profit:,.2f}\n"

    # 收益率
    if pool['total'] > 0:
        return_rate = (total_profit / pool['total']) * 100
        output += f"- **总收益率**: {return_rate:.2f}%\n\n"

    # 单笔收益明细
    if sell_operations:
        output += f"## 📋 单笔收益明细\n\n"
        output += "| 时间 | 价格 | 数量 | 成交金额 | 成本 | 盈亏 | 收益率 |\n"
        output += "|------|------|------|----------|------|------|--------|\n"

        for op in reversed(sell_operations):
            time_str = op.get('timestamp', '')[:16].replace('T', ' ')
            price = op.get('price', 0)
            qty = op.get('quantity', 0)
            amount = op.get('amount', 0)
            cost = op.get('cost', 0)
            profit = op.get('profit', 0)
            return_rate = (profit / cost * 100) if cost > 0 else 0

            profit_str = f"+¥{profit:,.2f}" if profit >= 0 else f"¥{profit:,.2f}"
            rate_str = f"+{return_rate:.2f}%" if return_rate >= 0 else f"{return_rate:.2f}%"

            output += f"| {time_str} | ¥{price:.2f} | {qty} | ¥{amount:,.2f} | ¥{cost:,.2f} | {profit_str} | {rate_str} |\n"

    return output


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description='📊 模拟盘交易系统 - 支持 A股/港股模拟交易',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
快速示例:
  python paper_trading.py init "赛力斯" --capital 100000
  python paper_trading.py init "赛力斯" --capital 50000 --force  # 强制重新初始化
  python paper_trading.py buy "赛力斯" --qty 500
  python paper_trading.py sell "赛力斯" --qty 200
  python paper_trading.py holdings "赛力斯"
  python paper_trading.py report "赛力斯" --type profit
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='命令')

    # init 命令
    init_parser = subparsers.add_parser('init', help='初始化资金池')
    init_parser.add_argument('stock_name', help='股票名称')
    init_parser.add_argument('--capital', '-c', type=float, required=True, help='初始资金')
    init_parser.add_argument('--code', help='股票代码（可选，会自动查询）')
    init_parser.add_argument('--force', '-f', action='store_true', help='强制删除现有资金池并重新初始化')

    # pool 命令
    pool_parser = subparsers.add_parser('pool', help='查询资金池状态')
    pool_parser.add_argument('stock_name', help='股票名称')

    # buy 命令
    buy_parser = subparsers.add_parser('buy', help='买入股票')
    buy_parser.add_argument('stock_name', help='股票名称')
    buy_parser.add_argument('--qty', '-q', type=int, help='买入股数')
    buy_parser.add_argument('--amount', '-a', type=float, help='买入金额')
    buy_parser.add_argument('--note', '-n', default='', help='备注信息')

    # sell 命令
    sell_parser = subparsers.add_parser('sell', help='卖出股票')
    sell_parser.add_argument('stock_name', help='股票名称')
    sell_parser.add_argument('--qty', '-q', type=int, help='卖出股数')
    sell_parser.add_argument('--all', action='store_true', help='全部卖出')
    sell_parser.add_argument('--note', '-n', default='', help='备注信息')

    # holdings 命令
    holdings_parser = subparsers.add_parser('holdings', help='查看持仓')
    holdings_parser.add_argument('stock_name', help='股票名称')

    # operations 命令
    operations_parser = subparsers.add_parser('operations', help='查看操作历史')
    operations_parser.add_argument('stock_name', help='股票名称')

    # report 命令
    report_parser = subparsers.add_parser('report', help='生成报告')
    report_parser.add_argument('stock_name', help='股票名称')
    report_parser.add_argument('--type', '-t', choices=['holdings', 'profit', 'history'],
                               required=True, help='报告类型')

    args = parser.parse_args()

    if args.command == 'init':
        init_capital_pool(args.stock_name, args.capital, args.code, args.force)

    elif args.command == 'pool':
        report = show_capital_pool(args.stock_name)
        print(report)

    elif args.command == 'buy':
        if not args.qty and not args.amount:
            print("❌ 请指定 --qty 或 --amount 参数")
            sys.exit(1)
        buy_stock(args.stock_name, args.qty, args.amount, args.note)

    elif args.command == 'sell':
        if not args.qty and not args.all:
            print("❌ 请指定 --qty 或 --all 参数")
            sys.exit(1)
        sell_stock(args.stock_name, args.qty, args.all, args.note)

    elif args.command == 'holdings':
        report = show_holdings(args.stock_name)
        print(report)

    elif args.command == 'operations':
        report = show_operations(args.stock_name)
        print(report)

    elif args.command == 'report':
        if args.type == 'holdings':
            report = show_holdings(args.stock_name)
        elif args.type == 'profit':
            report = show_profit(args.stock_name)
        elif args.type == 'history':
            report = show_operations(args.stock_name)
        print(report)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
