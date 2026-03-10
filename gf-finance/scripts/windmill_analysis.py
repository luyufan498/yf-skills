#!/usr/bin/env python3
"""
沪深指数估值分析工具
提供指数估值和ETF联动分析功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import GFMCPClient

class WindmillAnalysis:
    """沪深指数估值分析工具"""

    def __init__(self):
        self.client = GFMCPClient()
        self.service = "windmill"

    def get_index_list(self, page=1, page_size=20):
        """获取指数列表"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}  # API使用0基页码，所以page-1
        )

    def get_index_detail(self, index_code):
        """获取指数详情 (使用列表功能筛选)"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": 0, "perPage": 100}  # 获取更多数据后筛选
        )

    def get_index_valuation(self, index_code):
        """获取指数估值数据"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": 0, "perPage": 100}  # 获取数据后筛选
        )

    def get_index_etf(self, index_code, page=1, page_size=20):
        """获取指数对应的ETF列表"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}
        )

    def get_broad_indexes(self, page=1, page_size=20):
        """获取宽基指数列表"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}
        )

    def get_industry_indexes(self, page=1, page_size=20):
        """获取行业指数列表"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}
        )

    def get_theme_indexes(self, page=1, page_size=20):
        """获取主题指数列表"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}
        )

    def search_index(self, keyword, page=1, page_size=20):
        """搜索指数"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}  # 实际API不支持关键词搜索
        )

    def get_valuation_ranking(self, category="all", page=1, page_size=50):
        """获取估值排行榜"""
        return self.client.call_tool(
            self.service,
            "valuation_windmill_get",
            {"page": page-1, "perPage": page_size}
        )

def main():
    """命令行接口"""
    if len(sys.argv) < 2:
        print("沪深指数估值分析工具")
        print("使用方法:")
        print("  python windmill_analysis.py list [page] [size]           # 获取指数列表")
        print("  python windmill_analysis.py detail <index_code>          # 获取指数详情")
        print("  python windmill_analysis.py valuation <index_code>        # 获取指数估值")
        print("  python windmill_analysis.py etf <index_code> [page] [size] # 获取指数ETF")
        print("  python windmill_analysis.py broad [page] [size]          # 获取宽基指数")
        print("  python windmill_analysis.py industry [page] [size]       # 获取行业指数")
        print("  python windmill_analysis.py theme [page] [size]          # 获取主题指数")
        print("  python windmill_analysis.py search <keyword> [page] [size] # 搜索指数")
        print("  python windmill_analysis.py ranking [category] [page] [size] # 获取估值排行")
        print("\n指数代码:")
        print("  000001 - 上证指数    000300 - 沪深300    000905 - 中证500")
        print("  000852 - 中证1000    399001 - 深证成指  399006 - 创业板指")
        print("  000688 - 科创50")
        print("\n示例:")
        print("  python windmill_analysis.py valuation 000300")
        print("  python windmill_analysis.py etf 000300")
        print("  python windmill_analysis.py ranking all 1 50")
        sys.exit(1)

    analysis = WindmillAnalysis()
    command = sys.argv[1]

    try:
        if command == "list":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = analysis.get_index_list(page, page_size)
        elif command == "detail":
            result = analysis.get_index_detail(sys.argv[2])
        elif command == "valuation":
            result = analysis.get_index_valuation(sys.argv[2])
        elif command == "etf":
            page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            page_size = int(sys.argv[4]) if len(sys.argv) > 4 else 20
            result = analysis.get_index_etf(sys.argv[2], page, page_size)
        elif command == "broad":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = analysis.get_broad_indexes(page, page_size)
        elif command == "industry":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = analysis.get_industry_indexes(page, page_size)
        elif command == "theme":
            page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            page_size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = analysis.get_theme_indexes(page, page_size)
        elif command == "search":
            keyword = sys.argv[2]
            page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            page_size = int(sys.argv[4]) if len(sys.argv) > 4 else 20
            result = analysis.search_index(keyword, page, page_size)
        elif command == "ranking":
            category = sys.argv[2] if len(sys.argv) > 2 else "all"
            page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            page_size = int(sys.argv[4]) if len(sys.argv) > 4 else 50
            result = analysis.get_valuation_ranking(category, page, page_size)
        else:
            print(f"未知命令: {command}", file=sys.stderr)
            sys.exit(1)

        print(result)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()