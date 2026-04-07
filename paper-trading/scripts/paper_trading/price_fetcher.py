"""股票价格数据抓取器

从腾讯财经获取A股和港股实时价格，从新浪财经获取美股实时价格
"""

from datetime import datetime
from typing import Dict, List, Optional
import requests
from paper_trading.models import StockInfo, MarketType


class StockPriceFetcher:
    """股票价格数据获取器"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def fetch_batch(self, stock_codes: List[str]) -> Dict[str, StockInfo]:
        """
        批量获取股票实时数据
        支持港股（HK）、深沪股票

        Args:
            stock_codes: 股票代码列表，如 ['hk00700', 'sz000001', 'sh600000']

        Returns:
            股票信息字典 {code: StockInfo}
        """
        results = {}
        hk_sh_sz = [c.lower() for c in stock_codes if c.lower().startswith(('hk', 'sh', 'sz'))]
        if not hk_sh_sz:
            return results

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
                if info and info.code in stock_codes:
                    results[info.code] = info

            return results

        except Exception as e:
            print(f"Error fetching from Tencent: {e}")
            return {}

    def get_realtime_price(self, stock_code: str) -> Optional[StockInfo]:
        """
        获取单只股票的实时价格
        自动选择合适的数据源

        Args:
            stock_code: 股票代码（支持A股、港股、美股）

        Returns:
            股票信息对象，如果失败返回None
        """
        code_lower = stock_code.lower()

        # 如果是美股，用新浪
        if code_lower.startswith(('gb_', 'us')):
            result = self.fetch_sina_stock_data([stock_code])
            if result:
                return result[0] if isinstance(result, list) else result.get(stock_code)
            return None

        # 如果是港股或A股，优先用腾讯
        if code_lower.startswith(('hk', 'sh', 'sz')):
            result = self.fetch_batch([stock_code])
            return result.get(stock_code)

        # 默认尝试腾讯
        result = self.fetch_batch([stock_code])
        return result.get(stock_code)

    def fetch_sina_stock_data(self, stock_codes: List[str]) -> Dict[str, StockInfo]:
        """
        从新浪获取股票实时数据
        支持A股（SZ/SH）、美股（gb_）

        Args:
            stock_codes: 股票代码列表，如 ['sz000001', 'sh600000', 'gb_aapl']

        Returns:
            股票数据字典，键为股票代码，值为StockInfo对象
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
                        if info and info.code in stock_codes:
                            results[info.code] = info

            return results

        except Exception as e:
            print(f"Error fetching from Sina: {e}")
            return {}

    def _parse_sina_data(self, code: str, data: str) -> Optional[StockInfo]:
        """
        解析新浪返回的股票数据

        Args:
            code: 股票代码
            data: 数据字符串

        Returns:
            StockInfo 对象
        """
        if not data or data.strip() == '':
            return None

        try:
            parts = data.split(',')

            # 美股数据 (30个以上字段且以gb_开头)
            if 'gb_' in code and len(parts) >= 30:
                return StockInfo(
                    code=code.lower(),
                    name=parts[0],
                    market=MarketType.US_STOCK,
                    current_price=float(parts[1]) if parts[1] and parts[1].replace('.', '').isdigit() else None,
                    pre_close=float(parts[26]) if len(parts) > 26 and parts[26] and parts[26].replace('.', '').isdigit() else None,
                    open_price=float(parts[5]) if len(parts) > 5 and parts[5] and parts[5].replace('.', '').isdigit() else None,
                    high=float(parts[8]) if len(parts) > 8 and parts[8] and parts[8].replace('.', '').isdigit() else None,
                    low=float(parts[9]) if len(parts) > 9 and parts[9] and parts[9].replace('.', '').isdigit() else None,
                    volume=parts[10],
                    date='',
                    time='',
                    source='sina'
                )

            # 沪深A股数据 (32个以上字段且不是美股)
            elif len(parts) >= 32:
                market = MarketType.A_SHARE
                if code.lower().startswith('sz'):
                    market = MarketType.A_SHARE  # 深市
                elif code.lower().startswith('sh'):
                    market = MarketType.A_SHARE  # 沪市

                return StockInfo(
                    code=code.lower(),
                    name=parts[0],
                    market=market,
                    current_price=float(parts[3]) if parts[3] else None,
                    pre_close=float(parts[2]) if parts[2] else None,
                    open_price=float(parts[1]) if parts[1] else None,
                    high=float(parts[4]) if parts[4] else None,
                    low=float(parts[5]) if parts[5] else None,
                    volume=parts[8],
                    date=parts[30] if len(parts) > 30 else '',
                    time=parts[31] if len(parts) > 31 else '',
                    source='sina'
                )

        except (ValueError, IndexError) as e:
            print(f"Error parsing Sina data for {code}: {e}")

        return None

    def _parse_tencent_data(self, line: str) -> Optional[StockInfo]:
        """
        解析腾讯返回的股票数据

        Args:
            line: 数据行

        Returns:
            StockInfo 对象
        """
        if '=' not in line:
            return None

        try:
            var_name, data = line.split('=', 1)
            code = var_name.replace('v_r_', '').replace('v_', '')

            parts = data.strip('"').strip(';').split('~')
            if len(parts) < 35:
                return None

            market = MarketType.A_SHARE
            if code.lower().startswith('hk'):
                market = MarketType.HK_STOCK
            elif code.lower().startswith(('gb_', 'us')):
                market = MarketType.US_STOCK

            return StockInfo(
                code=code.lower(),
                name=parts[1],
                market=market,
                current_price=float(parts[3]) if parts[3] else None,
                pre_close=float(parts[4]) if parts[4] else None,
                open_price=float(parts[5]) if parts[5] else None,
                high=float(parts[33]) if len(parts) > 33 else None,
                low=float(parts[34]) if len(parts) > 34 else None,
                volume=parts[6],
                date=parts[29].replace('/', '-') if len(parts) > 29 else '',
                time=':'.join(parts[30].split(':')[:3]) if len(parts) > 30 else '',
                source='tencent'
            )

        except (ValueError, IndexError) as e:
            print(f"Error parsing Tencent data: {e}")
            return None


def fetch_stock_info(stock_name: str, code: Optional[str] = None) -> Optional[StockInfo]:
    """
    获取股票信息（集成代码查询和价格获取）

    Args:
        stock_name: 股票名称
        code: 股票代码（可选）

    Returns:
        StockInfo 对象
    """
    from paper_trading.code_searcher import StockCodeSearcher

    if code is None:
        searcher = StockCodeSearcher()
        results = searcher.search_cn_stocks(stock_name, limit=3)
        if results:
            code = results[0].get('code', '')
        else:
            return None

    if not code:
        return None

    fetcher = StockPriceFetcher()
    return fetcher.get_realtime_price(code)
