#!/usr/bin/env python3
"""
财务对比分析工具
提供上市公司财务数据对比分析功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import GFMCPClient

class QuantAnalysis:
    """财务对比分析工具"""

    def __init__(self):
        self.client = GFMCPClient()
        self.service = "quant"

    def get_basic_data(self, stock_codes):
        """获取基本指标对比（单个或多个股票）"""
        return self.client.call_tool(
            self.service,
            "commonBasic",
            {"stock_codes": stock_codes}
        )

    def compare_indicators(self, stock_codes, report_type=12, year="2022"):
        """两个股票对比指标"""
        return self.client.call_tool(
            self.service,
            "commonIndicator",
            {
                "report_type": report_type,
                "stock_codes": stock_codes,
                "year": year
            }
        )

    def get_industry_info(self, stock_codes):
        """获取股票行业信息"""
        return self.client.call_tool(
            self.service,
            "commonIndustryInfo",
            {"stock_codes": stock_codes}
        )

    def get_industry_top2(self, stock_code):
        """获取股票所在行业所有指标前二"""
        return self.client.call_tool(
            self.service,
            "commonIndustryTop2",
            {"stock_code": stock_code}
        )

    def get_common_report_type(self, stock_codes):
        """获取两个股票公共最近的报告期"""
        return self.client.call_tool(
            self.service,
            "commonReportType",
            {"stock_codes": stock_codes}
        )

    def get_trend(self, stock_code, cycle="1y"):
        """获取单个股票PB/PE走势图"""
        return self.client.call_tool(
            self.service,
            "commonTrend",
            {"cycle": cycle, "stock_code": stock_code}
        )

    def get_aggregation(self, stock_code):
        """聚合查询"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorAggregation",
            {"stock_code": stock_code}
        )

    def get_bank_data(self, stock_code, report_type=12):
        """获取银行专项指标"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorBank",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def get_cashflow(self, stock_code, report_type=12):
        """获取现金流量表"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorCashflow",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def get_insurance_data(self, stock_code, report_type=12):
        """获取保险专项指标"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorInsurance",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def get_balance_sheet(self, stock_code, report_type=12):
        """获取资产负债表"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorLiabilty",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def get_main_business(self, stock_code):
        """获取主营业务构成饼图"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorMainBusiness",
            {"stock_code": stock_code}
        )

    def get_profit(self, stock_code, report_type=12):
        """获取利润表"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorProfit",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def get_securities_data(self, stock_code, report_type=12):
        """获取证券专项指标"""
        return self.client.call_tool(
            self.service,
            "majorIndicatorSecurities",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def analyze_profit_ability(self, stock_code, report_type=12):
        """分析股票的盈利能力"""
        return self.client.call_tool(
            self.service,
            "analyzeProfitAbility",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def analyze_capital_structure(self, stock_code, report_type=12):
        """分析股票的资本结构"""
        return self.client.call_tool(
            self.service,
            "analyzeCapitalStructure",
            {"report_type": report_type, "stock_code": stock_code}
        )

    def analyze_cashflow(self, stock_code, report_type=12):
        """分析股票的现金流量"""
        return self.client.call_tool(
            self.service,
            "analyzeCrashflow",
            {"report_type": report_type, "stock_code": stock_code}
        )

def main():
    """命令行接口"""
    if len(sys.argv) < 2:
        print("财务对比分析工具")
        print("使用方法:")
        print("  python quant_analysis.py basic <code1> [code2]        # 获取基本指标")
        print("  python quant_analysis.py compare <code1> <code2> [year] # 对比指标")
        print("  python quant_analysis.py industry <code1> [code2]      # 获取行业信息")
        print("  python quant_analysis.py toptwo <code>                  # 获取行业前二")
        print("  python quant_analysis.py report <code1> <code2>         # 获取公共报告期")
        print("  python quant_analysis.py trend <code> [cycle]            # 获取走势图")
        print("  python quant_analysis.py bank <code> [report_type]     # 银行专项指标")
        print("  python quant_analysis.py cashflow <code> [report_type]  # 现金流量表")
        print("  python quant_analysis.py balance <code> [report_type]   # 资产负债表")
        print("  python quant_analysis.py profit <code> [report_type]    # 利润表")
        print("  python quant_analysis.py main_business <code>            # 主营业务构成")
        print("  python quant_analysis.py securities <code> [report_type] # 证券专项指标")
        print("  python quant_analysis.py insurance <code> [report_type]  # 保险专项指标")
        print("  python quant_analysis.py profit_ability <code> [report_type] # 盈利能力分析")
        print("  python quant_analysis.py capital_structure <code> [report_type] # 资本结构分析")
        print("  python quant_analysis.py cashflow_analysis <code> [report_type] # 现金流量分析")
        print("\n示例:")
        print("  python quant_analysis.py basic SH600000 SZ000776")
        print("  python quant_analysis.py compare SH600000 SZ000776 2023")
        sys.exit(1)

    analysis = QuantAnalysis()
    command = sys.argv[1]

    try:
        if command == "basic":
            stock_codes = [sys.argv[2]] if len(sys.argv) == 3 else [sys.argv[2], sys.argv[3]]
            result = analysis.get_basic_data(stock_codes)
        elif command == "compare":
            year = sys.argv[4] if len(sys.argv) > 4 else "2022"
            result = analysis.compare_indicators([sys.argv[2], sys.argv[3]], year=year)
        elif command == "industry":
            stock_codes = [sys.argv[2]] if len(sys.argv) == 3 else [sys.argv[2], sys.argv[3]]
            result = analysis.get_industry_info(stock_codes)
        elif command == "toptwo":
            result = analysis.get_industry_top2(sys.argv[2])
        elif command == "report":
            result = analysis.get_common_report_type([sys.argv[2], sys.argv[3]])
        elif command == "trend":
            cycle = sys.argv[3] if len(sys.argv) > 3 else "1y"
            result = analysis.get_trend(sys.argv[2], cycle)
        elif command == "bank":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.get_bank_data(sys.argv[2], report_type)
        elif command == "cashflow":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.get_cashflow(sys.argv[2], report_type)
        elif command == "balance":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.get_balance_sheet(sys.argv[2], report_type)
        elif command == "profit":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.get_profit(sys.argv[2], report_type)
        elif command == "main_business":
            result = analysis.get_main_business(sys.argv[2])
        elif command == "securities":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.get_securities_data(sys.argv[2], report_type)
        elif command == "insurance":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.get_insurance_data(sys.argv[2], report_type)
        elif command == "profit_ability":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.analyze_profit_ability(sys.argv[2], report_type)
        elif command == "capital_structure":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.analyze_capital_structure(sys.argv[2], report_type)
        elif command == "cashflow_analysis":
            report_type = int(sys.argv[3]) if len(sys.argv) > 3 else 12
            result = analysis.analyze_cashflow(sys.argv[2], report_type)
        else:
            print(f"未知命令: {command}", file=sys.stderr)
            sys.exit(1)

        print(result)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()