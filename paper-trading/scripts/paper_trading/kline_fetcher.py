"""K线数据抓取器

支持A股、港股、美股的K线数据和分时数据获取
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests


class KLineDataFetcher:
    """K线数据抓取器"""

    def __init__(self, timeout: int = 15):
        """初始化抓取器

        Args:
            timeout: 请求超时时间（秒）
        """
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
                    inner_data = stock_data['data']
                    if isinstance(inner_data, dict):
                        minute_data = inner_data.get('data', [])
                        date = inner_data.get('date', '')
                    elif isinstance(inner_data, list) and len(inner_data) > 0:
                        date = inner_data[0] if isinstance(inner_data[0], str) else ''
                        minute_data = inner_data[1] if len(inner_data) > 1 else []
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

                    if recent_minutes is not None and recent_minutes > 0:
                        result['data'] = result['data'][-recent_minutes:]

            return result

        except Exception as e:
            print(f"Error fetching minute data for {stock_code}: {e}")
            return {'code': stock_code, 'date': '', 'data': []}

    def _is_us_stock(self, stock_code: str) -> bool:
        """
        判断是否为美股代码

        Args:
            stock_code: 股票代码

        Returns:
            是否为美股
        """
        code = stock_code.strip()

        if code.lower().startswith(('gb_', 'us')):
            return True

        # 纯字母代码（1-5个字母）且不以数字开头，判定为美股
        if code.replace('.', '').replace('-', '').isalpha() and len(code) <= 5:
            if not code.lower().startswith(('sh', 'sz', 'bj', 'hk')):
                return True

        return False

    def fetch_kline_data(self, stock_code: str, kline_type: str = 'day', count: int = 120) -> List[dict]:
        """
        获取K线数据

        Args:
            stock_code: 股票代码
            kline_type: K线类型，可选值:
                        'minute'(分时数据), 'day'(日K), 'week'(周K), 'month'(月K),
                        '5min', '10min', '15min', '30min', '60min'(分钟K线)
            count: 获取最近N条数据

        Returns:
            K线数据列表
        """
        # 美股使用 YFinance 专用处理
        if self._is_us_stock(stock_code):
            return self._fetch_us_stock_kline(stock_code, kline_type, count)

        # 分钟级K线需要基于分时数据采样
        minute_kline_map = {
            '5min': 5,
            '10min': 10,
            '15min': 15,
            '30min': 30,
            '60min': 60
        }

        if kline_type in minute_kline_map:
            minutes_per_kline = minute_kline_map[kline_type]
            minute_count = count * minutes_per_kline + minutes_per_kline
            minute_result = self.fetch_minute_data(stock_code, recent_minutes=minute_count)
            klines = self._sample_minute_to_kline(minute_result['data'], minutes_per_kline,
                                                minute_result.get('date', ''))
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
                if isinstance(data['data'], list) and len(data['data']) == 0:
                    return []

                if not isinstance(data['data'], dict):
                    return []

                stock_data = data['data'].get(stock_code, {})
                if stock_data:
                    if kline_type == 'week':
                        kline_field = 'qfqweek'
                    elif kline_type == 'month':
                        kline_field = 'qfqmonth'
                    else:
                        kline_field = 'qfqday'
                    day_data = stock_data.get(kline_field, stock_data.get(kline_type, []))

                    def safe_float(val):
                        if val is None:
                            return None
                        if isinstance(val, (int, float)):
                            return float(val)
                        if isinstance(val, str):
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return None
                        if isinstance(val, dict):
                            return list(val.values())[0] if val and val.values() else None
                        return None

                    for item in day_data:
                        if item and len(item) >= 6:
                            kline_list.append({
                                'date': item[0],
                                'open': safe_float(item[1]),
                                'close': safe_float(item[2]),
                                'high': safe_float(item[3]),
                                'low': safe_float(item[4]),
                                'volume': safe_float(item[5]),
                                'amount': safe_float(item[6]) if len(item) > 6 else None
                            })

                    kline_list.sort(key=lambda x: x['date'])

            return kline_list

        except Exception as e:
            print(f"Error fetching K-line data for {stock_code}: {e}")
            return []

    def _fetch_us_stock_kline(self, stock_code: str, kline_type: str = 'day', count: int = 120) -> List[dict]:
        """
        使用 YFinance 获取美股K线数据

        Args:
            stock_code: 美股代码
            kline_type: K线类型
            count: 数据条数

        Returns:
            K线数据列表
        """
        try:
            import yfinance as yf
        except ImportError:
            print("Error: yfinance 库未安装。安装：pip install yfinance")
            return []

        # 转换代码格式
        code = stock_code.upper().strip()

        # 去除前缀
        if code.startswith('GB_'):
            code = code[3:]
        elif code.startswith('US'):
            code = code[2:]

        # 去除可能的后缀
        code = code.replace('.OQ', '').replace('.N', '').replace('.PK', '')

        # 美股指数映射
        index_map = {
            'SPX': '^GSPC',      # 标普500
            'DJI': '^DJI',       # 道琼斯
            'IXIC': '^IXIC',     # 纳斯达克
            'VIX': '^VIX',       # 波动率指数
            'RUT': '^RUT',       # 罗素2000
            'NDX': '^NDX',       # 纳斯达克100
        }

        yf_symbol = index_map.get(code, code)

        # 映射K线类型到yfinance period
        period_map = {
            'day': '1d',
            'week': '1wk',
            'month': '1mo',
            '5min': '5m',
            '15min': '15m',
            '30min': '30m',
            '60min': '1h',
        }

        yf_period = period_map.get(kline_type, '1d')

        # 计算起始日期
        end_date = datetime.now()

        if kline_type in ['5min', '15min', '30min', '60min']:
            start_date = end_date - timedelta(days=30)
        elif kline_type == 'week':
            start_date = end_date - timedelta(days=180)
        elif kline_type == 'month':
            start_date = end_date - timedelta(days=730)
        else:  # day
            if count <= 30:
                start_date = end_date - timedelta(days=45)
            elif count <= 90:
                start_date = end_date - timedelta(days=120)
            else:
                start_date = end_date - timedelta(days=365 * 2)

        try:
            print(f"使用 YFinance 获取美股 {stock_code} ({yf_symbol}) - {kline_type} 数据...")

            import pandas as pd
            df = yf.download(
                tickers=yf_symbol,
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d'),
                interval=yf_period,
                progress=False,
                auto_adjust=True,
                prepost=True,
            )

            if df.empty:
                print(f"警告：未找到 {stock_code} ({yf_symbol}) 的数据")
                return []

            # 正确处理 MultiIndex 数据结构
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.reset_index()

            kline_list = []

            for _, row in df.iterrows():
                date_value = row.get('Date') if 'Date' in row else row.get('Datetime')

                kline_item = {
                    'date': date_value.strftime('%Y-%m-%d') if hasattr(date_value, 'strftime') else str(date_value),
                    'open': float(row['Open']),
                    'close': float(row['Close']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'volume': int(row['Volume']),
                    'amount': None
                }

                # 对于分钟数据，使用不同的时间格式
                if kline_type in ['5min', '15min', '30min', '60min']:
                    if hasattr(date_value, 'strftime'):
                        time_str = date_value.strftime('%H:%M')
                        kline_item['date'] = date_value.strftime('%Y-%m-%d')
                        kline_item['time'] = time_str
                    else:
                        kline_item['date'] = str(date_value)
                        kline_item['time'] = str(date_value)

                kline_list.append(kline_item)

            if len(kline_list) > count:
                kline_list = kline_list[-count:]

            return kline_list

        except Exception as e:
            print(f"错误：获取美股 {stock_code} 数据失败：{e}")
            return []

    def _sample_minute_to_kline(self, minute_data: List[dict], minutes_per_kline: int, date: str = '') -> List[dict]:
        """
        将分时数据采样为K线数据

        Args:
            minute_data: 分时数据列表
            minutes_per_kline: 每根K线的分钟数
            date: 交易日期

        Returns:
            K线数据列表
        """
        if not minute_data:
            return []

        kline_map = {}

        for item in minute_data:
            time_str = item['time'].replace(':', '')
            if len(time_str) >= 4 and time_str[:4].isdigit():
                hour = int(time_str[:2])
                minute_value = int(time_str[2:4])

                total_minutes_from_930 = (hour - 9) * 60 + (minute_value - 30)
                kline_index = total_minutes_from_930 // minutes_per_kline

                if kline_index >= 0:
                    if kline_index not in kline_map:
                        kline_map[kline_index] = {
                            'time': item['time'],
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
