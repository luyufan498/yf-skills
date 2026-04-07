"""股票代码查询器

从新浪财经搜索A股股票代码
"""

from typing import List
import requests


class StockCodeSearcher:
    """股票代码查询器"""

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
