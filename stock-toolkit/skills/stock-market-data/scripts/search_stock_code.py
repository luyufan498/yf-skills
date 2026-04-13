#!/usr/bin/env python3
"""
股票代码查询脚本
支持搜索A股、港股、美股的股票代码
"""
import json
import requests
from typing import List, Dict, Optional
import re


class StockCodeSearcher:
    """股票代码查询器"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
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
        搜索A股股票代码

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

            # 解析响应
            # 格式: var suggestdata="平安银行,11,000001,sz000001,平安银行,,平安银行,99,1,ESG,,;..."
            if 'suggestdata="' in content:
                data_str = content.split('suggestdata="')[1].split('";')[0]

                results = []
                for item in data_str.split(';'):
                    if not item:
                        continue

                    parts = item.split(',')
                    if len(parts) >= 5:
                        # parts: [名称, 类型(11), 代码, 完整代码, 名称2, ...]
                        name = parts[0]
                        stock_type = parts[1]
                        code = parts[2]  # 6位代码
                        full_code = parts[3]  # 完整代码如sz000001

                        # 类型映射
                        type_map = {'11': 'A股', '12': '港股', '13': '美股'}
                        market = type_map.get(stock_type, '其他')

                        # 格式化代码
                        if full_code:
                            formatted_code = full_code.lower()
                        elif len(code) == 6:
                            # 根据代码前缀判断市场
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

        # 搜索港股
        for name, code in self.hot_stocks['hk'].items():
            if keyword.lower() in name.lower() or keyword in code.lower():
                results.append({
                    'name': name,
                    'code': code,
                    'market': '港股',
                    'source': '内置数据库'
                })

        # 搜索美股
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

    def get_stock_code_guide(self) -> str:
        """获取股票代码格式说明"""
        return """
股票代码格式说明：

A股（中国内地）：
  - 上海证券交易所: sh6位代码，如 sh600000、sh000001
  - 深圳证券交易所: sz6位代码/3位代码，如 sz000001、sz300001
  - 北京证券交易所: bj8位代码，如 bj832566

港股（香港）：
  - 格式: hk5位代码，如 hk00700、hk09988
  - 示例: hk00700(腾讯控股)、hk03690(美团)

美股：
  - 格式: gb_代码 或 us代码，如 gb_aapl、gb_goog
  - 常用代码: gb_aapl(苹果)、gb_goog(谷歌)、gb_msft(微软)、gb_tsla(特斯拉)

查找代码建议：
1. A股: 使用本脚本搜索功能
2. 港股/美股: 访问新浪财经、东方财富查看
3. 或在浏览器直接搜索"股票名+股票代码"
"""


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description='股票代码查询工具')
    parser.add_argument('keyword', type=str, help='搜索关键词（股票名称或代码）')
    parser.add_argument('-n', '--number', type=int, default=10,
                       help='每个市场返回的结果数量')
    parser.add_argument('--guide', action='store_true',
                       help='显示股票代码格式说明')
    parser.add_argument('--all', action='store_true',
                       help='搜索所有市场（包括常用港股/美股）')
    parser.add_argument('-f', '--format', choices=['text', 'json'], default='text',
                       help='输出格式')

    args = parser.parse_args()

    searcher = StockCodeSearcher()

    if args.guide:
        print(searcher.get_stock_code_guide())
        return

    # 综合搜索
    all_results = []
    all_results.extend(searcher.search_cn_stocks(args.keyword, args.all and args.number or args.number))
    if args.all:
        all_results.extend(searcher.search_hot_stocks(args.keyword, args.number))

    # JSON 格式输出
    if args.format == 'json':
        output = {
            'keyword': args.keyword,
            'total': len(all_results),
            'results': all_results
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # 文本格式输出
    print(f"\n正在搜索: {args.keyword}")
    print("=" * 60)

    # 搜索A股
    cn_results = searcher.search_cn_stocks(args.keyword, args.number)
    if cn_results:
        print(f"\n【A股搜索结果】（{len(cn_results)} 条）")
        for i, stock in enumerate(cn_results, 1):
            market_tag = f"[{stock['market']}]" if stock['market'] != 'A股' else ""
            print(f"  {i}. {stock['name']}{market_tag}")
            print(f"     代码: {stock['code']}")
    else:
        print(f"\n【A股搜索结果】未找到匹配的股票")

    if args.all:
        # 搜索常用港股/美股
        hot_results = searcher.search_hot_stocks(args.keyword, args.number)
        if hot_results:
            print(f"\n【港股/美股搜索结果】（{len(hot_results)} 条）")
            for i, stock in enumerate(hot_results, 1):
                print(f"  {i}. {stock['name']} [{stock['market']}]")
                print(f"     代码: {stock['code']} ({stock['source']})")
        else:
            print(f"\n【港股/美股搜索结果】未找到匹配的股票")

    # 提示
    print(f"\n提示: 使用 --guide 查看股票代码格式说明")
    print(f"      使用 --all 搜索所有市场（包括港股/美股）")


if __name__ == '__main__':
    main()