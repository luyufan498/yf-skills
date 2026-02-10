#!/usr/bin/env python3
"""
市场新闻数据抓取脚本
支持多个新闻源：财联社、新浪财经等
"""
import json
import requests
from typing import Dict, List, Optional
from datetime import datetime


class MarketNewsFetcher:
    """市场新闻数据抓取器"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }

    def fetch_cls_news(self, limit: int = 30) -> List[dict]:
        """
        获取财联社电报新闻

        Args:
            limit: 获取新闻数量

        Returns:
            新闻列表
        """
        url = "https://www.cls.cn/nodeapi/telegraphList"
        headers = {
            'Referer': 'https://www.cls.cn/',
            **self.headers
        }

        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
            data = response.json()

            if data.get('error') == 0:
                roll_data = data.get('data', {}).get('roll_data', [])
                news_list = []

                for item in roll_data[:limit]:
                    ctime = item.get('ctime', 0)
                    data_time = datetime.fromtimestamp(ctime)

                    news = {
                        'title': item.get('title', ''),
                        'content': item.get('content', ''),
                        'time': data_time.strftime('%H:%M:%S'),
                        'date': data_time.strftime('%Y-%m-%d'),
                        'datetime': data_time.isoformat(),
                        'url': item.get('shareurl', ''),
                        'source': '财联社电报',
                        'is_red': item.get('level', 'C') != 'C',
                        'tags': []
                    }

                    # 提取标签
                    subjects = item.get('subjects', [])
                    if subjects:
                        news['tags'] = [s.get('subject_name', '') for s in subjects if isinstance(s, dict)]

                    news_list.append(news)

                return news_list

        except Exception as e:
            print(f"Error fetching Cailianpress news: {e}")

        return []

    def fetch_sina_live_news(self, limit: int = 20) -> List[dict]:
        """
        获取新浪财经直播新闻

        Args:
            limit: 获取新闻数量

        Returns:
            新闻列表
        """
        url = f"https://zhibo.sina.com.cn/api/zhibo/feed"
        headers = {
            'Referer': 'https://finance.sina.com.cn',
            **self.headers
        }

        params = {
            'page': 1,
            'page_size': limit,
            'zhibo_id': 152,
            'tag_id': 0,
            'dire': 'f',
            'dpc': 1,
            'pagesize': limit,
            'id': 4161089,
            'type': 0,
            '_': int(datetime.now().timestamp())
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            data = response.json()

            # 新浪API现在的数据结构: result.data.feed.dict{'list': [...]}
            result = data.get('result', {}).get('data', {})
            feed_obj = result.get('feed', {})

            # 兼容新旧格式：feed 可能是 dict 或 list
            if isinstance(feed_obj, dict):
                feed_list = feed_obj.get('list', [])
            else:
                feed_list = feed_obj if isinstance(feed_obj, list) else []

            news_list = []
            for item in feed_list:
                title_match = item.get('rich_text', '').split('】')

                create_time = item.get('create_time', '')
                if ' ' in create_time:
                    date, time = create_time.split(' ')
                else:
                    date, time = '', ''

                news = {
                    'content': item.get('rich_text', ''),
                    'title': title_match[0].replace('【', '') + '】' if len(title_match) > 1 else '',
                    'time': time,
                    'date': date,
                    'datetime': datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").isoformat() if create_time else '',
                    'source': '新浪财经',
                    'tags': []
                }

                # 提取标签
                tags = item.get('tag', []) if isinstance(item.get('tag'), list) else []
                news['tags'] = [t.get('name', '') if isinstance(t, dict) else str(t) for t in tags]

                # 判断是否重要（包含"焦点"标签）
                if '焦点' in news['tags']:
                    news['is_red'] = True

                news_list.append(news)

            return news_list

        except Exception as e:
            print(f"Error fetching Sina live news: {e}")

        return []

    def fetch_tradingview_news(self, limit: int = 10) -> List[dict]:
        """
        获取TradingView中文新闻

        Args:
            limit: 获取新闻数量

        Returns:
            新闻列表
        """
        url = "https://news-mediator.tradingview.com/news-flow/v2/news"
        headers = {
            'Host': 'news-mediator.tradingview.com',
            'Origin': 'https://cn.tradingview.com',
            'Referer': 'https://cn.tradingview.com/',
            **self.headers
        }

        params = {
            'filter': 'lang:zh-Hans',
            'client': 'screener',
            'streaming': 'false'
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=self.timeout * 1.5)
            data = response.json()

            items = data.get('items', [])[:limit]
            news_list = []

            for item in items:
                published = item.get('published', 0)
                data_time = datetime.fromtimestamp(published)

                # 获取新闻详情
                news_id = item.get('id', '')
                # TradingView API格式变化：title 现在直接在根层级，之前在 headline.text
                title = item.get('title', '') if item.get('title') else ''
                # 如果没有title，尝试旧格式
                if not title:
                    headline = item.get('headline', {})
                    title = headline.get('text', '') if isinstance(headline, dict) else str(headline)

                news = {
                    'id': news_id,
                    'title': title,
                    'content': title,  # TradingView通常只有标题，用title作为content
                    'description': '',
                    'time': data_time.strftime('%H:%M:%S'),
                    'date': data_time.strftime('%Y-%m-%d'),
                    'datetime': data_time.isoformat(),
                    'url': f"https://cn.tradingview.com/news/{news_id}",
                    'source': 'TradingView外媒',
                    'is_red': False,
                    'tags': item.get('tags', [])
                }

                news_list.append(news)

            return news_list

        except Exception as e:
            print(f"Error fetching TradingView news: {e}")

        return []

    def fetch_all_news(self, limit_per_source: int = 20) -> Dict[str, List[dict]]:
        """
        获取所有源的新闻

        Args:
            limit_per_source: 每个源获取的新闻数量

        Returns:
            包含所有源新闻的字典
        """
        return {
            'cls_news': self.fetch_cls_news(limit_per_source),
            'sina_live': self.fetch_sina_live_news(limit_per_source),
            'tradingview': self.fetch_tradingview_news(limit_per_source // 2)
        }

    def get_latest_news(self, total_limit: int = 30) -> List[dict]:
        """
        获取最新新闻（合并所有源）

        Args:
            total_limit: 总新闻数量

        Returns:
            按时间排序的新闻列表
        """
        all_sources = self.fetch_all_news(limit_per_source=total_limit)

        # 合并所有新闻
        all_news = []
        for source, news_list in all_sources.items():
            for news in news_list:
                news['source_type'] = source
                all_news.append(news)

        # 按时间排序
        all_news.sort(key=lambda x: x.get('datetime', ''), reverse=True)

        return all_news[:total_limit]


def main():
    """命令行入口"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='Market News Fetcher')
    parser.add_argument('-n', '--number', type=int, default=10, help='Number of news items')
    parser.add_argument('--source', choices=['all', 'cls', 'sina', 'tv'], default='all',
                        help='News source: cls (Cailianpress), sina (Sina Finance), tv (TradingView), all')
    parser.add_argument('-f', '--format', choices=['json', 'simple'], default='simple',
                        help='Output format')

    args = parser.parse_args()
    fetcher = MarketNewsFetcher()

    # 根据参数获取新闻
    if args.source == 'cls':
        news = fetcher.fetch_cls_news(args.number)
    elif args.source == 'sina':
        news = fetcher.fetch_sina_live_news(args.number)
    elif args.source == 'tv':
        news = fetcher.fetch_tradingview_news(args.number)
    else:
        news = fetcher.get_latest_news(args.number)

    # 输出结果
    if args.format == 'json':
        print(json.dumps(news, indent=2, ensure_ascii=False))
    else:
        print(f"\n=== 最新市场新闻 ({args.source.upper()}) ===\n")
        for i, item in enumerate(news, 1):
            print(f"{i}. [{item['time']}] {item['source']}")
            if item.get('title'):
                print(f"   标题: {item['title']}")
            print(f"   内容: {item['content'][:200]}...")
            if item.get('tags'):
                print(f"   标签: {', '.join(item['tags'])}")
            print()


if __name__ == '__main__':
    main()