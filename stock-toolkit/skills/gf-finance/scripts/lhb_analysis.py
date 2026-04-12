#!/usr/bin/env python3
"""
龙虎榜分析工具
提供龙虎榜相关数据查询功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import GFMCPClient

class LHBAnalysis:
    """龙虎榜分析工具"""

    def __init__(self):
        self.client = GFMCPClient()
        self.service = "lhb"

    def get_daily_stocks(self, date, market):
        """获取指定日期和市场上榜的个股列表"""
        return self.client.call_tool(
            self.service,
            "lhb_aborttrade_market_date_get",
            {"date": date, "market": market}
        )

    def get_stock_detail(self, code, date, market):
        """获取指定日期和个股上榜的明细"""
        return self.client.call_tool(
            self.service,
            "lhb_aborttrade_market_code_date_get",
            {"code": str(code), "date": date, "market": market}
        )

    def get_stock_history(self, code, market):
        """获取指定个股上榜历史"""
        return self.client.call_tool(
            self.service,
            "lhb_aborttrade_stock_market_code_get",
            {"code": str(code), "market": market}
        )

    def get_top_stocks(self, months="m3", page=1, page_size=10):
        """获取指定时间区间内上榜个股排行列表"""
        return self.client.call_tool(
            self.service,
            "lhb_stat_stock_months_get",
            {"months": months, "page": page, "pageSize": page_size}
        )

    def get_stock_stats(self, code, market, months="m6"):
        """获取指定个股在指定时间区间内的统计数据"""
        return self.client.call_tool(
            self.service,
            "lhb_stat_stock_market_code_months_get",
            {"code": str(code), "market": market, "months": months}
        )

    def get_dept_stats(self, dept_id, months="m12"):
        """获取指定营业部在指定时间区间内的统计数据"""
        return self.client.call_tool(
            self.service,
            "lhb_stat_dept_id_months_get",
            {"id": str(dept_id), "months": months}
        )

    def get_outline(self, plate="lhb"):
        """获取龙虎榜、评级概括"""
        return self.client.call_tool(
            self.service,
            "lhb_outline_plate_get",
            {"plate": plate}
        )

    def get_calendar(self, market, month):
        """获取龙虎榜日历"""
        return self.client.call_tool(
            self.service,
            "lhb_calendar_market_month_get",
            {"market": market, "month": month}
        )

    def get_batch_stocks(self, date, months="m1", page=1, page_size=10):
        """获取个股列表上龙虎榜情况"""
        return self.client.call_tool(
            self.service,
            "lhb_aborttrade_batchstock_post",
            {"date": date, "months": months, "page": page, "pageSize": page_size}
        )

def main():
    """命令行接口"""
    if len(sys.argv) < 2:
        print("龙虎榜分析工具")
        print("使用方法:")
        print("  python lhb_analysis.py daily <date> <market>         # 获取指定日期上榜股票")
        print("  python lhb_analysis.py detail <code> <date> <market>  # 获取个股上榜明细")
        print("  python lhb_analysis.py history <code> <market>      # 获取个股上榜历史")
        print("  python lhb_analysis.py top [months] [page] [size]    # 获取上榜个股排行")
        print("  python lhb_analysis.py stats <code> <market> [months] # 获取个股统计数据")
        print("  python lhb_analysis.py dept <dept_id> [months]      # 获取营业部统计数据")
        print("  python lhb_analysis.py outline [plate]               # 获取龙虎榜概括")
        print("  python lhb_analysis.py calendar <market> <month>      # 获取龙虎榜日历")
        print("\n示例:")
        print("  python lhb_analysis.py daily 20250528 sh")
        print("  python lhb_analysis.py top m3 1 10")
        sys.exit(1)

    analysis = LHBAnalysis()
    command = sys.argv[1]

    try:
        if command == "daily":
            result = analysis.get_daily_stocks(int(sys.argv[2]), sys.argv[3])
        elif command == "detail":
            result = analysis.get_stock_detail(sys.argv[2], int(sys.argv[3]), sys.argv[4])
        elif command == "history":
            result = analysis.get_stock_history(sys.argv[2], sys.argv[3])
        elif command == "top":
            months = sys.argv[2] if len(sys.argv) > 2 else "m3"
            page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            page_size = int(sys.argv[4]) if len(sys.argv) > 4 else 10
            result = analysis.get_top_stocks(months, page, page_size)
        elif command == "stats":
            months = sys.argv[4] if len(sys.argv) > 4 else "m6"
            result = analysis.get_stock_stats(sys.argv[2], sys.argv[3], months)
        elif command == "dept":
            months = sys.argv[3] if len(sys.argv) > 3 else "m12"
            result = analysis.get_dept_stats(sys.argv[2], months)
        elif command == "outline":
            plate = sys.argv[2] if len(sys.argv) > 2 else "lhb"
            result = analysis.get_outline(plate)
        elif command == "calendar":
            result = analysis.get_calendar(sys.argv[2], int(sys.argv[3]))
        else:
            print(f"未知命令: {command}", file=sys.stderr)
            sys.exit(1)

        print(result)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()