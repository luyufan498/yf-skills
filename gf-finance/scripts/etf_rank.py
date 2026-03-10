#!/usr/bin/env python3
"""
热门ETF榜单工具
提供 ETF 排行榜查询功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import GFMCPClient

class ETFRank:
    """热门ETF榜单工具"""

    def __init__(self):
        self.client = GFMCPClient()
        self.service = "etf_rank"

    def get_rise_rank(self, page=1, page_size=20):
        """获取涨幅榜 (type=1)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "1", "size": page_size, "page": page-1}  # API使用0基页码
        )

    def get_fall_rank(self, page=1, page_size=20):
        """获取跌幅榜 (type=2)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "2", "size": page_size, "page": page-1}
        )

    def get_fund_rank(self, page=1, page_size=20):
        """获取主力资金榜 (type=4)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "4", "size": page_size, "page": page-1}
        )

    def get_feature_rank(self, page=1, page_size=20):
        """获取换手榜 (type=3)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "3", "size": page_size, "page": page-1}
        )

    def get_hot_rank(self, page=1, page_size=20):
        """获取关注榜 (type=6)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "6", "size": page_size, "page": page-1}
        )

    def search_etf(self, keyword, page=1, page_size=20):
        """搜索ETF (type=5 搜索榜)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "5", "size": page_size, "page": page-1}  # 注意：实际API可能不支持关键词搜索
        )

    def get_five_day_rise_rank(self, page=1, page_size=20):
        """获取5日涨幅榜 (type=7)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "7", "size": page_size, "page": page-1}
        )

    def get_five_day_fall_rank(self, page=1, page_size=20):
        """获取5日跌幅榜 (type=8)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "8", "size": page_size, "page": page-1}
        )

    def get_continual_rise_rank(self, page=1, page_size=20, limit=0):
        """获取连涨榜 (type=9)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "9", "size": page_size, "page": page-1, "continueRiseLimit": limit}
        )

    def get_continual_fall_rank(self, page=1, page_size=20):
        """获取连跌榜 (type=10)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "10", "size": page_size, "page": page-1}
        )

    def get_five_day_fund_rank(self, page=1, page_size=20):
        """获取5日主力资金榜 (type=11)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "11", "size": page_size, "page": page-1}
        )

    def get_net_subscription_rank(self, page=1, page_size=20):
        """获取净申购榜 (type=12)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "12", "size": page_size, "page": page-1}
        )

    def get_premium_rate_rank(self, page=1, page_size=20):
        """获取溢价率榜 (type=13)"""
        return self.client.call_tool(
            self.service,
            "finance-api_product_etf_rank_get",
            {"type": "13", "size": page_size, "page": page-1}
        )

def main():
    """命令行接口"""
    if len(sys.argv) < 2:
        print("热门ETF榜单工具")
        print("使用方法:")
        print("  python etf_rank.py rise [page] [size]     # 获取涨幅榜")
        print("  python etf_rank.py fall [page] [size]     # 获取跌幅榜")
        print("  python etf_rank.py fund [page] [size]     # 获取资金榜")
        print("  python etf_rank.py feature [page] [size]  # 获取特色榜")
        print("  python etf_rank.py hot [page] [size]      # 获取热门榜")
        print("  python etf_rank.py search <keyword> [page] [size]  # 搜索ETF")
        print("\n示例:")
        print("  python etf_rank.py rise 1 20")
        print("  python etf_rank.py search 芯片")
        sys.exit(1)

    rank = ETFRank()
    command = sys.argv[1]

    try:
        if command == "rise":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = rank.get_rise_rank(page, page_size)
        elif command == "fall":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = rank.get_fall_rank(page, page_size)
        elif command == "fund":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = rank.get_fund_rank(page, page_size)
        elif command == "feature":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = rank.get_feature_rank(page, page_size)
        elif command == "hot":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = rank.get_hot_rank(page, page_size)
        elif command == "search":
            keyword = sys.argv[2]
            page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            page_size = int(sys.argv[4]) if len(sys.argv) > 4 else 20
            result = rank.search_etf(keyword, page, page_size)
        else:
            print(f"未知命令: {command}", file=sys.stderr)
            sys.exit(1)

        print(result)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()