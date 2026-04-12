"""股票代码查询器

支持搜索A股、港股、美股的股票代码
"""

from typing import List, Dict, Optional
import requests


class StockValidationError(Exception):
    """股票名称验证失败异常"""
    pass


def validate_stock_name(stock_name: str) -> tuple[bool, Optional[str]]:
    """
    验证股票名称是否合法

    使用 StockCodeSearcher 查询股票名称是否存在

    Args:
        stock_name: 股票名称

    Returns:
        (是否合法, 股票代码) 元组，如果不合法则代码为 None
    """
    if not stock_name or not stock_name.strip():
        return False, None

    searcher = StockCodeSearcher()
    results = searcher.search_cn_stocks(stock_name, limit=1)

    if results:
        # 找到匹配的股票，返回 True 和股票代码
        return True, results[0]['code']
    return False, None


class StockCodeSearcher:
    """股票代码查询器"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }

        # 常用港股/美股代码库
        self.hot_stocks = {
            'hk': {
                '腾讯控股': 'hk00700',
                '中国移动': 'hk00941',
                '阿里巴巴-SW': 'hk09988',
                '美团-W': 'hk03690',
                '小米集团-W': 'hk01810',
                '比亚迪股份': 'hk01211',
                '京东集团-SW': 'hk09618',
                '网易-S': 'hk09999',
                '中芯国际': 'hk00981',
                '工商银行': 'hk01398',
                '建设银行': 'hk00939',
                '中国银行': 'hk03988',
                '招商银行': 'hk03968',
                '中国平安': 'hk02318',
                '中国石油股份': 'hk00857',
                '汇丰控股': 'hk00005',
                '长江实业': 'hk01113',
            },
            'gb': {
                '苹果': 'gb_aapl',
                '谷歌': 'gb_goog',
                '谷歌A类股': 'gb_googl',
                '微软': 'gb_msft',
                '亚马逊': 'gb_amzn',
                '特斯拉': 'gb_tsla',
                '英伟达': 'gb_nvda',
                'Meta(FB)': 'gb_meta',
                '伯克希尔-哈撒韦': 'gb_brk_a',
                '强生': 'gb_jnj',
                '可口可乐': 'gb_ko',
                '麦当劳': 'gb_mcd',
                '迪士尼': 'gb_dis',
                '耐克': 'gb_nke',
            }
        }

    def search_cn_stocks(self, keyword: str, limit: int = 20) -> List[dict]:
        """
        搜索A股、港股、美股股票代码

        Args:
            keyword: 搜索关键词（股票名称或代码）
            limit: 返回结果数量

        Returns:
            股票列表
        """
        # 使用新浪财经suggest API
        # type=11:A股, 12:港股, 13:美股, 14:概念
        url = f"https://suggest3.sinajs.cn/suggest/type=11,12,13&key={keyword}&name=suggestdata"

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
                        stock_type = parts[1]
                        code = parts[2]
                        full_code = parts[3]

                        type_map = {'11': 'A股', '12': '港股', '13': '美股'}
                        market = type_map.get(stock_type, '其他')

                        if full_code:
                            formatted_code = full_code.lower()
                        elif len(code) == 6:
                            if code.startswith('6'):
                                formatted_code = f'sh{code}'
                            elif code.startswith(('0', '3')):
                                formatted_code = f'sz{code}'
                            elif code.startswith('8'):
                                formatted_code = f' bj{code}'
                            else:
                                formatted_code = code
                        else:
                            formatted_code = code

                        results.append({
                            'name': name,
                            'code': formatted_code,
                            'original_code': code,
                            'full_code': full_code,
                            'market': market,
                            'source': '新浪财经'
                        })

                return results[:limit]

            return []

        except Exception as e:
            print(f"Error searching stocks: {e}")
            return []

    def search_hot_stocks(self, keyword: str, limit: int = 20) -> List[dict]:
        """
        在常用股票库中搜索（港股、美股）

        Args:
            keyword: 搜索关键词
            limit: 返回结果数量

        Returns:
            股票列表
        """
        keyword = keyword.lower()
        results = []

        for name, code in self.hot_stocks['hk'].items():
            if keyword.lower() in name.lower() or keyword in code.lower():
                results.append({
                    'name': name,
                    'code': code,
                    'market': '港股',
                    'source': '内置数据库'
                })

        for name, code in self.hot_stocks['gb'].items():
            if keyword.lower() in name.lower() or keyword in code.lower():
                results.append({
                    'name': name,
                    'code': code,
                    'market': '美股',
                    'source': '内置数据库'
                })

        return results[:limit]

    def search(self, keyword: str, limit: int = 20) -> Dict[str, List[dict]]:
        """
        综合搜索股票代码

        Args:
            keyword: 搜索关键词
            limit: 每个市场的返回数量

        Returns:
            按市场分类的搜索结果
        """
        results = {
            'A_share': self.search_cn_stocks(keyword, limit),
            'hot_funds': self.search_hot_stocks(keyword, limit)
        }

        return results
