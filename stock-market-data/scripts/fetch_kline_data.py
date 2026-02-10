#!/usr/bin/env python3
"""
K线数据抓取脚本
支持实时分时数据和K线数据（日K、周K、月K、分钟K线）

功能说明：
- 分时数据: 每一笔成交 tick 数据
- 日K/周K/月K: K线数据（前复权）
- 分钟K线(5min/10min/15min/30min/60min): 基于分时数据采样生成，格式与分时数据一致，包含 time 字段
- -c/--count 参数: 控制返回的数据数量（分时数据为分钟数，K线为条数）
"""
import json
import requests
from typing import List, Dict, Optional
from datetime import datetime


class KLineDataFetcher:
    """K线数据抓取器"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
        }

    def fetch_minute_data(self, stock_code: str, recent_minutes: Optional[int] = None) -> Dict:
        """
        获取分时数据

        Args:
            stock_code: 股票代码，如 'sh600000', 'sz000001', 'hk00700'
            recent_minutes: 返回最近N分钟数据，None表示返回全部

        Returns:
            分时数据字典，包含 date 和 data 列表
        """
        # 美股特殊处理
        if stock_code.lower().startswith(('gb_', 'us')):
            # 转换代码 gb_aapl -> usAAPL.OQ
            code = stock_code.upper().replace('GB_', 'us') + '.OQ'
            url = f"https://web.ifzq.gtimg.cn/appstock/app/UsMinute/query?code={code}"
        else:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={stock_code}"

        try:
            response = requests.get(
                url,
                headers={'Host': 'web.ifzq.gtimg.cn', **self.headers},
                timeout=self.timeout
            )
            data = response.json()

            result = {
                'code': stock_code,
                'date': '',
                'data': []
            }

            if data.get('code') == 0 and 'data' in data:
                stock_data = data['data'].get(stock_code, {})
                if stock_data and 'data' in stock_data:
                    # data['data'] 可能直接是列表 (包含: [date, [data_list], ...])
                    inner_data = stock_data['data']
                    if isinstance(inner_data, dict):
                        minute_data = inner_data.get('data', [])
                        date = inner_data.get('date', '')
                    elif isinstance(inner_data, list) and len(inner_data) > 0:
                        date = inner_data[0] if isinstance(inner_data[0], str) else ''
                        minute_data = inner_data[1] if len(inner_data) > 1 else []
                        # 如果minute_data还是字典结构（比如包含data键），继续提取
                        if isinstance(minute_data, dict):
                            minute_data = minute_data.get('data', [])
                    else:
                        minute_data = []

                    for item in minute_data:
                        parts = item.replace('\r\n', ' ').split() if isinstance(item, str) else []
                        if len(parts) >= 3:
                            time_str = parts[0][:4] + ':' + parts[0][4:6] if len(parts[0]) >= 6 else parts[0]
                            price = float(parts[1]) if parts[1].replace('.', '').isdigit() else None
                            volume = float(parts[2]) if parts[2].replace('.', '').isdigit() else None
                            amount = float(parts[3]) if len(parts) > 3 and parts[3].replace('.', '').isdigit() else 0

                            result['data'].append({
                                'time': time_str,
                                'price': price,
                                'volume': volume,
                                'amount': amount
                            })

                    result['date'] = date

                    # 如果指定了recent_minutes，只返回最近的N分钟数据
                    if recent_minutes is not None and recent_minutes > 0:
                        result['data'] = result['data'][-recent_minutes:]

            return result

        except Exception as e:
            print(f"Error fetching minute data for {stock_code}: {e}")
            return {'code': stock_code, 'date': '', 'data': []}

    def fetch_kline_data(self, stock_code: str, kline_type: str = 'day', count: int = 120) -> List[dict]:
        """
        获取K线数据

        Args:
            stock_code: 股票代码
            kline_type: K线类型，可选值:
                        'minute'(分时数据), 'day'(日K), 'week'(周K), 'month'(月K),
                        '5min', '10min', '15min', '30min', '60min'(分钟K线，基于分时数据采样)
            count: 获取最近N条数据

        Returns:
            K线数据列表
            对于基于分时采样的K线，格式与分时数据一致，包含 'time' 字段
        """
        # 分钟级K线需要基于分时数据采样
        minute_kline_map = {
            '5min': 5,
            '10min': 10,
            '15min': 15,
            '30min': 30,
            '60min': 60
        }

        if kline_type in minute_kline_map:
            # 获取分时数据并采样为K线
            # 分时数据获取数量 = K线条数 * 每根K线分钟数 + 一些缓冲(多取20%)
            minutes_per_kline = minute_kline_map[kline_type]
            minute_count = count * minutes_per_kline + minutes_per_kline  # 多加一个周期作为缓冲
            minute_result = self.fetch_minute_data(stock_code, recent_minutes=minute_count)
            klines = self._sample_minute_to_kline(minute_result['data'], minutes_per_kline,
                                                minute_result.get('date', ''))
            # 只返回最后N条K线
            return klines[-count:] if len(klines) > count else klines

        # 日K/周K/月K 从API直接获取
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={stock_code},{kline_type},,,{count},qfq"

        try:
            response = requests.get(
                url,
                headers={'Host': 'web.ifzq.gtimg.cn', **self.headers},
                timeout=self.timeout
            )
            data = response.json()

            kline_list = []

            if data.get('code') == 0 and 'data' in data:
                # 检查data是否为dict，如果为空列表则打印错误信息
                if isinstance(data['data'], list) and len(data['data']) == 0:
                    print(f"Error: K-line data not available for {stock_code} (likely unsupported market)")
                    return []

                if not isinstance(data['data'], dict):
                    print(f"Error: Unexpected data format for {stock_code}")
                    return []

                stock_data = data['data'].get(stock_code, {})
                if stock_data:
                    # 根据K线类型选择正确的字段
                    if kline_type == 'week':
                        kline_field = 'qfqweek'
                    elif kline_type == 'month':
                        kline_field = 'qfqmonth'
                    else:
                        kline_field = 'qfqday'
                    day_data = stock_data.get(kline_field, stock_data.get(kline_type, []))

                    for item in day_data:
                        if item and len(item) >= 6:
                            kline_list.append({
                                'date': item[0],
                                'open': float(item[1]) if item[1] else None,
                                'close': float(item[2]) if item[2] else None,
                                'high': float(item[3]) if item[3] else None,
                                'low': float(item[4]) if item[4] else None,
                                'volume': float(item[5]) if item[5] else None,
                                'amount': float(item[6]) if len(item) > 6 and item[6] else None
                            })

                    # 按日期升序排列
                    kline_list.sort(key=lambda x: x['date'])

            return kline_list

        except Exception as e:
            print(f"Error fetching K-line data for {stock_code}: {e}")
            return []

    def _sample_minute_to_kline(self, minute_data: List[dict], minutes_per_kline: int, date: str = '') -> List[dict]:
        """
        将分时数据采样为K线数据
        格式与分时数据保持一致：包含 'time' 字段

        Args:
            minute_data: 分时数据列表
            minutes_per_kline: 每根K线的分钟数 (5, 10, 15, 30, 60)
            date: 交易日期

        Returns:
            K线数据列表，每条记录包含 time, open, high, low, close, volume, amount
        """
        if not minute_data:
            return []

        kline_map = {}

        for item in minute_data:
            time_str = item['time'].replace(':', '')  # 处理 "09:30" 或 "0930" 格式
            if len(time_str) >= 4 and time_str[:4].isdigit():
                hour = int(time_str[:2])
                minute_value = int(time_str[2:4])

                # 确定K线索引 (从9:30开始的交易日)
                # 9:30-9:34 -> 0, 9:35-9:39 -> 1 for 5min
                total_minutes_from_930 = (hour - 9) * 60 + (minute_value - 30)
                kline_index = total_minutes_from_930 // minutes_per_kline

                if kline_index >= 0:  # 忽略9:30之前的数据
                    if kline_index not in kline_map:
                        kline_map[kline_index] = {
                            'time': item['time'],  # 保留第一条数据的时间作为K线起始时间
                            'open': item['price'],
                            'high': item['price'],
                            'low': item['price'],
                            'close': item['price'],
                            'volume': item['volume'] or 0,
                            'amount': item['amount'] or 0
                        }
                    else:
                        kline = kline_map[kline_index]
                        if item['price']:
                            kline['high'] = max(kline['high'], item['price'])
                            kline['low'] = min(kline['low'], item['price'])
                            kline['close'] = item['price']
                        kline['volume'] = (kline['volume'] or 0) + (item['volume'] or 0)
                        kline['amount'] = (kline['amount'] or 0) + (item['amount'] or 0)

        # 转换为列表并按时间排序
        kline_list = []
        for key in sorted(kline_map.keys()):
            kline = kline_map[key]
            kline_list.append({
                'time': kline['time'],
                'open': kline['open'],
                'close': kline['close'],
                'high': kline['high'],
                'low': kline['low'],
                'volume': kline['volume'],
                'amount': kline['amount']
            })

        return kline_list

    def get_latest_price(self, stock_code: str) -> Optional[Dict]:
        """
        获取最新价格（通过K线数据）

        Args:
            stock_code: 股票代码

        Returns:
            最新价格信息
        """
        kline_data = self.fetch_kline_data(stock_code, 'day', 1)

        if kline_data:
            return {
                'code': stock_code,
                'date': kline_data[0]['date'],
                'price': kline_data[0]['close'],
                'open': kline_data[0]['open'],
                'high': kline_data[0]['high'],
                'low': kline_data[0]['low'],
                'volume': kline_data[0]['volume']
            }

        return None

    def get_stock_summary(self, stock_code: str) -> Dict:
        """
        获取股票汇总信息（包括分时和K线）

        Args:
            stock_code: 股票代码

        Returns:
            股票汇总信息
        """
        return {
            'code': stock_code,
            'kline_day': self.fetch_kline_data(stock_code, 'day', 5),
            'kline_week': self.fetch_kline_data(stock_code, 'week', 20),
            'kline_5min': self.fetch_kline_data(stock_code, '5min', 48),  # 最后48根5分钟K线
            'minute': self.fetch_minute_data(stock_code)
        }


def main():
    """命令行入口"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description='K-Line Data Fetcher: Support minute, 5min, 10min, 15min, 30min, 60min, day, week, month data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n'
               '  %(prog)s sh600000 -t minute -c 10        # Last 10 minutes of tick data\n'
               '  %(prog)s sh600000 -t 5min -c 48         # Last 48 5-minute K-lines (4 hours)\n'
               '  %(prog)s sh600000 -t 10min -c 24        # Last 24 10-minute K-lines (4 hours)\n'
               '  %(prog)s sh600000 -t 15min -c 16        # Last 16 15-minute K-lines (4 hours)\n'
               '  %(prog)s sh600000 -t day -c 5           # Last 5 trading days of daily K-line\n'
               '  %(prog)s sh600000 -t week -c 20         # Last 20 weeks of weekly K-line\n'
               '  %(prog)s sh600000 --summary             # Get all data types summary'
    )
    parser.add_argument('code', type=str, help='Stock code (e.g., sh600000, sz000001, hk00700)')
    parser.add_argument('-t', '--type',
                       choices=['minute', '5min', '10min', '15min', '30min', '60min', 'day', 'week', 'month'],
                       default='day', help='Data type')
    parser.add_argument('-c', '--count', type=int, default=30,
                       help='Count: N data to return (minutes for minute type, K-lines for K-line types)')
    parser.add_argument('--summary', action='store_true',
                       help='Get summary of all data types')

    args = parser.parse_args()
    fetcher = KLineDataFetcher()

    if args.summary:
        result = fetcher.get_stock_summary(args.code)
    elif args.type == 'minute':
        result = fetcher.fetch_minute_data(args.code, recent_minutes=args.count)
    else:
        result = fetcher.fetch_kline_data(args.code, args.type, args.count)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()