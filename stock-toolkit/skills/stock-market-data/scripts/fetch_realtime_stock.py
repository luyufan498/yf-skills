#!/usr/bin/env python3
"""
实时股票价格数据抓取脚本
支持沪深A股、港股、美股的实时价格查询
"""
import json
import requests
from typing import Dict, Optional, List
from datetime import datetime


class StockPriceFetcher:
    """股票价格数据抓取器"""

    def __init__(self, timeout: int = 10):
        """
        初始化抓取器

        Args:
            timeout: 请求超时时间（秒）
        """
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0'
        }

    def fetch_sina_stock_data(self, stock_codes: List[str]) -> Dict[str, dict]:
        """
        从新浪获取股票实时数据
        支持A股（SZ/SH）、美股（gb_）

        Args:
            stock_codes: 股票代码列表，如 ['sz000001', 'sh600000', 'gb_aapl']

        Returns:
            股票数据字典，键为股票代码，值为股票信息
        """
        # 将代码列表转换为新浪格式
        formatted_codes = []
        for code in stock_codes:
            code_lower = code.lower()
            if code_lower.startswith('us'):
                # 美股代码转换 usAAPL -> gb_aapl
                formatted_codes.append(code_lower.replace('us', 'gb_', 1))
            elif code_lower.startswith('gb_'):
                formatted_codes.append(code_lower)
            else:
                formatted_codes.append(code_lower)

        codes_str = ','.join(formatted_codes)
        url = f"http://hq.sinajs.cn/rn={int(datetime.now().timestamp())}&list={codes_str}"

        try:
            # 新浪API需要Referer头才能正常访问
            headers = {
                'Referer': 'https://finance.sina.com.cn/',
                **self.headers
            }
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.encoding = 'gb18030'
            results = {}

            for line in response.text.strip().split('\n'):
                if not line:
                    continue

                # 解析数据行格式: hq_str_sh600001="..."
                if '="' in line:
                    var_name, data = line.split('=', 1)
                    var_name = var_name.replace('var ', '').strip()
                    data = data.strip('"').strip(';')

                    # 提取股票代码
                    if var_name.startswith('hq_str_'):
                        stock_code = var_name[7:]  # 去掉 'hq_str_' (7个字符)
                        info = self._parse_sina_data(stock_code, data)
                        if info:
                            if info['code'] in stock_codes:
                                results[info['code']] = info

            return results

        except Exception as e:
            print(f"Error fetching from Sina: {e}")
            return {}

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

        # 过滤出港股和沪深代码
        hk_codes = [c.lower() for c in stock_codes if c.lower().startswith(('hk', 'sh', 'sz'))]
        if not hk_codes:
            return results

        # 转换代码格式
        formatted_codes = []
        for code in hk_codes:
            code_lower = code.lower()
            if code_lower.startswith('hk'):
                # 港股: HK00700 -> r_hk00700
                formatted_codes.append('r_' + code_lower)
            else:
                # A股: sz000001 -> sz000001
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

                # 解析腾讯数据
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
        自动选择合适的数据源

        Args:
            stock_code: 股票代码（支持A股、港股、美股）

        Returns:
            股票信息字典，如果失败返回None
        """
        code_lower = stock_code.lower()

        # 如果是港股或A股，优先用腾讯
        if code_lower.startswith(('hk', 'sh', 'sz')):
            result = self.fetch_tencent_stock_data([stock_code])
            return result.get(stock_code)

        # 如果是美股，用新浪
        result = self.fetch_sina_stock_data([stock_code])
        return result.get(stock_code)

    def _parse_sina_data(self, code: str, data: str) -> Optional[dict]:
        """
        解析新浪返回的股票数据

        Args:
            code: 股票代码
            data: 数据字符串

        Returns:
            解析后的股票信息
        """
        if not data or data.strip() == '':
            return None

        try:
            parts = data.split(',')

            # 美股数据 (30个以上字段且以gb_开头)
            if 'gb_' in code and len(parts) >= 30:
                return {
                    'code': code.lower(),  # 统一使用小写以匹配stock_codes
                    'name': parts[0],
                    'price': float(parts[1]) if parts[1] and parts[1].replace('.', '').isdigit() else None,
                    'change_percent': float(parts[2]) if parts[2] and parts[2].replace('.', '').replace('-', '').isdigit() else None,
                    'change': float(parts[4]) if len(parts) > 4 and parts[4] and parts[4].replace('.', '').replace('-', '').isdigit() else None,
                    'date_time': parts[3] if len(parts) > 3 else '',  # 日期时间字符串
                    'open': float(parts[5]) if len(parts) > 5 and parts[5] and parts[5].replace('.', '').isdigit() else None,
                    'high': float(parts[8]) if len(parts) > 8 and parts[8] and parts[8].replace('.', '').isdigit() else None,
                    'low': float(parts[9]) if len(parts) > 9 and parts[9] and parts[9].replace('.', '').isdigit() else None,
                    'volume': parts[10],  # 成交量
                    'pre_close': float(parts[26]) if len(parts) > 26 and parts[26] and parts[26].replace('.', '').isdigit() else None,
                    'source': 'sina'
                }

            # 沪深A股数据 (32个以上字段且不是美股)
            elif len(parts) >= 32:
                return {
                    'code': code.lower(),  # 统一使用小写以匹配stock_codes
                    'name': parts[0],
                    'open': float(parts[1]) if parts[1] else None,
                    'pre_close': float(parts[2]) if parts[2] else None,
                    'price': float(parts[3]) if parts[3] else None,
                    'high': float(parts[4]) if parts[4] else None,
                    'low': float(parts[5]) if parts[5] else None,
                    'bid': float(parts[6]) if parts[6] else None,
                    'ask': float(parts[7]) if parts[7] else None,
                    'volume': parts[8],
                    'amount': parts[9],
                    'date': parts[30] if len(parts) > 30 else '',
                    'time': parts[31] if len(parts) > 31 else '',
                    'source': 'sina'
                }
                return {
                    'code': code.lower(),  # 统一使用小写以匹配stock_codes
                    'name': parts[0],
                    'price': float(parts[1]) if parts[1] and parts[1].replace('.', '').isdigit() else None,
                    'change_percent': float(parts[2]) if parts[2] and parts[2].replace('.', '').replace('-', '').isdigit() else None,
                    'change': float(parts[4]) if len(parts) > 4 and parts[4] and parts[4].replace('.', '').replace('-', '').isdigit() else None,
                    'date_time': parts[3] if len(parts) > 3 else '',  # 日期时间字符串
                    'open': float(parts[5]) if len(parts) > 5 and parts[5] and parts[5].replace('.', '').isdigit() else None,
                    'high': float(parts[8]) if len(parts) > 8 and parts[8] and parts[8].replace('.', '').isdigit() else None,
                    'low': float(parts[9]) if len(parts) > 9 and parts[9] and parts[9].replace('.', '').isdigit() else None,
                    'volume': parts[10],  # 成交量
                    'pre_close': float(parts[26]) if len(parts) > 26 and parts[26] and parts[26].replace('.', '').isdigit() else None,
                    'source': 'sina'
                }

        except (ValueError, IndexError) as e:
            print(f"Error parsing Sina data for {code}: {e}")

        return None

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
            # 提取代码 v_r_hk00700 或 v_sh600001
            code = var_name.replace('v_r_', '').replace('v_', '')

            parts = data.strip('"').strip(';').split('~')
            if len(parts) < 35:
                return None

            # 港股和A股数据解析
            return {
                'code': code.lower(),  # 统一使用小写以匹配stock_codes
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


def main():
    """命令行入口"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python fetch_realtime_stock.py <stock_code> [<stock_code>...]")
        print("Examples:")
        print("  python fetch_realtime_stock.py sz000001")
        print("  python fetch_realtime_stock.py sh600000 sz000001 hk00700")
        sys.exit(1)

    stock_codes = sys.argv[1:]
    fetcher = StockPriceFetcher()

    # 批量获取
    results = {}

    # 先用腾讯获取港股和A股
    hk_sh_sz = [c for c in stock_codes if c.lower().startswith(('hk', 'sh', 'sz'))]
    if hk_sh_sz:
        results.update(fetcher.fetch_tencent_stock_data(hk_sh_sz))

    # 再用新浪获取美股和其他
    others = [c for c in stock_codes if c.lower() not in results]
    if others:
        results.update(fetcher.fetch_sina_stock_data(others))

    # 输出结果
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()