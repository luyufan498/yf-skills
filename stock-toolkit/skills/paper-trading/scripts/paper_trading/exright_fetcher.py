"""除权除息信息抓取器

从腾讯前复权K线接口提取除权除息事件
"""

import re
from typing import List, Dict, Optional
import requests


class ExRightFetcher:
    """除权除息信息抓取器"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
        }

    def fetch_exright_events(self, stock_code: str, lookback_days: int = 500) -> List[Dict]:
        """
        从腾讯前复权K线中提取除权除息事件

        Args:
            stock_code: 股票代码，如 'sh600000', 'sz000001'
            lookback_days: 查询天数

        Returns:
            除权除息事件列表，每个事件包含:
            - cqr: 除权除息日
            - nd: 年度
            - djr: 股权登记日
            - fhcontent: 方案内容
            - bonus_per_10: 每10股派息（元）
            - split_per_10: 每10股送转股数
        """
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={stock_code},day,,,{lookback_days},qfq"

        try:
            response = requests.get(
                url,
                headers={'Host': 'web.ifzq.gtimg.cn', **self.headers},
                timeout=self.timeout
            )
            data = response.json()
        except Exception:
            return []

        if data.get('code') != 0 or 'data' not in data:
            return []

        stock_data = data['data'].get(stock_code, {})
        qfqday = stock_data.get('qfqday', [])

        events = []
        for day in qfqday:
            if len(day) != 7:
                continue

            exright_info = day[6]
            if not isinstance(exright_info, dict):
                continue

            fhcontent = exright_info.get('FHcontent', '')
            if not fhcontent:
                continue

            bonus_per_10, split_per_10 = self._parse_fhcontent(fhcontent)

            events.append({
                'cqr': exright_info.get('cqr', ''),
                'nd': exright_info.get('nd', ''),
                'djr': exright_info.get('djr', ''),
                'fhcontent': fhcontent,
                'bonus_per_10': bonus_per_10,
                'split_per_10': split_per_10,
            })

        return events

    @staticmethod
    def _parse_fhcontent(content: str) -> tuple[float, float]:
        """
        解析分红送转方案

        Examples:
            '10派1.7元转3股' -> (1.7, 3)
            '10派308.76元' -> (308.76, 0)
            '10送5派2元' -> (2.0, 5)
            '10转3股派1.7元' -> (1.7, 3)

        Returns:
            (bonus_per_10, split_per_10)
        """
        bonus_per_10 = 0.0
        split_per_10 = 0.0

        # 提取分红: 派X元（不限于"10派"格式）
        bonus_match = re.search(r'派(\d+\.?\d*)元', content)
        if bonus_match:
            bonus_per_10 = float(bonus_match.group(1))

        # 提取送转股: 10转X股 / 10送X股 / 转X股 / 送X股
        split_match = re.search(r'(?:10)?(?:转|送)(\d+\.?\d*)', content)
        if split_match:
            split_per_10 = float(split_match.group(1))

        return bonus_per_10, split_per_10
