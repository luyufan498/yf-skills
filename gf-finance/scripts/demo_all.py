#!/usr/bin/env python3
"""
广发证券 MCP 服务 - 所有脚本功能演示
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from mcp_client import GFMCPClient

def parse_result(result):
    """解析API返回结果（可能是字符串或字典）"""
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return result
    return result

def demo_all_services():
    """演示所有可用的服务功能"""
    print('\n' + '=' * 80)
    print('🚀 广发证券 MCP 服务 - 所有脚本功能演示')
    print('=' * 80)

    client = GFMCPClient()

    # 1. LHB 龙虎榜脚本
    print('\n📊 1. LHB 龙虎榜分析工具')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('lhb', 'lhb_outline_plate_get', {'plate': 'lhb'}))
        if 'data' in result:
            data = result['data']
            print(f'✅ 龙虎榜概括:')
            print(f'   上榜数量: {data.get("num", 0)}')
            print(f'   统计日期: {data.get("date", "N/A")}')
            if 'item' in data and data['item']:
                top_stock = data['item'][0]
                print(f'   上榜股票: {top_stock.get("secuSht", "N/A")} ({top_stock.get("trdCode", "N/A")})')
                print(f'   涨跌幅: {top_stock.get("dayChgRat", 0):.2f}%')
                print(f'   成交额: {top_stock.get("tnvVal", 0) / 1e8:.2f}亿元')
    except Exception as e:
        print(f'❌ 失败: {e}')

    # 2. Windmill 指数估值脚本
    print('\n📈 2. Windmill 指数估值分析工具')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('windmill', 'valuation_windmill_get', {'page': 0, 'perPage': 3}))
        if 'data' in result and 'list' in result['data']:
            indexes = result['data']['list']
            print(f'✅ 指数估值榜单 (前3个):')
            for idx, index in enumerate(indexes, 1):
                print(f'   {idx}. {index.get("indexName", "N/A")} ({index.get("indexCode", "N/A")})')
                print(f'      PE分位: {index.get("pePercent", 0):.2f}%  PB分位: {index.get("pbPercent", 0):.2f}%')
                print(f'      推荐ETF: {index.get("fundName", "N/A")} ({index.get("fundCode", "N/A")})')
    except Exception as e:
        print(f'❌ 失败: {e}')

    # 3. ETF 排行脚本
    print('\n💹 3. ETFRank 热门ETF榜单工具')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('etf_rank', 'finance-api_product_etf_rank_get', {'type': '1', 'size': 3, 'page': 0}))
        if 'data' in result and isinstance(result['data'], list):
            etfs = result['data']
            print(f'✅ ETF 涨幅榜 (前3名):')
            for idx, etf in enumerate(etfs, 1):
                print(f'   {idx}. {etf.get("name", "N/A")} ({etf.get("code", "N/A")})')
                print(f'      涨跌幅: {etf.get("roc", 0):.2f}%  规模: {etf.get("fundSize", "N/A")}')
                print(f'      换手率: {etf.get("turnover_rate", 0):.2f}%  溢价率: {etf.get("premium", 0):.2f}%')
    except Exception as e:
        print(f'❌ 失败: {e}')

    # 4. Quant 财务分析脚本
    print('\n💰 4. Quant 财务对比分析工具')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('quant', 'common_basic_post', {'stock_codes': ['SH600000']}))
        if 'data' in result and isinstance(result['data'], list):
            stock_data = result['data'][0]
            print(f'✅ 基本指标查询:')
            print(f'   股票: {stock_data.get("stock_name", "N/A")} ({stock_data.get("stock_code", "N/A")})')
            if 'basic' in stock_data:
                basic = stock_data['basic']
                print(f'   上市日期: {basic.get("list_date", "N/A")}')
                print(f'   总市值: {basic.get("total_marketcap", 0):.2f}亿元')
            if 'valuation' in stock_data:
                valuation = stock_data['valuation']
                print(f'   市盈率PE: {valuation.get("pettm", 0):.2f}  (行业均值: {valuation.get("pettm_avg", 0):.2f})')
                print(f'   市净率PB: {valuation.get("pb", 0):.2f}  (行业均值: {valuation.get("pb_avg", 0):.2f})')
    except Exception as e:
        print(f'❌ 失败: {e}')

    # 5. Quant 行业信息
    print('\n🏭 5. Quant 行业信息查询')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('quant', 'common_industry_info_post', {'stock_codes': ['SH600000']}))
        if 'data' in result and isinstance(result['data'], list):
            industry = result['data'][0]
            print(f'✅ 行业信息:')
            print(f'   股票: {industry.get("stock_name", "N/A")} ({industry.get("stock_code", "N/A")})')
            print(f'   所属行业: {industry.get("industry_name", "N/A")} ({industry.get("industry_code", "N/A")})')
            if 'compare_pe' in industry:
                print(f'   PE相近股票: {industry["compare_pe"].get("stock_name", "N/A")} ({industry["compare_pe"].get("stock_code", "N/A")})')
            if 'compare_marketcap' in industry:
                print(f'   市值相近股票: {industry["compare_marketcap"].get("stock_name", "N/A")} ({industry["compare_marketcap"].get("stock_code", "N/A")})')
    except Exception as e:
        print(f'❌ 失败: {e}')

    # 6. Quant 行业Top2
    print('\n🏆 6. Quant 行业指标Top2')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('quant', 'common_industry_top2_get', {'stock_code': 'SZ000776'}))
        if 'data' in result:
            data = result['data']
            print(f'✅ 行业指标Top2:')
            print(f'   股票: SZ000776 (广发证券)')
            print(f'   行业: {data.get("industry_name", "N/A")} ({data.get("industry_code", "N/A")})')
            if 'data' in data:
                for item in data['data'][:3]:
                    indicator = item.get('indicator', 'N/A')
                    top2 = item.get('top2', [])
                    print(f'   {indicator}:')
                    for rank, stock in enumerate(top2, 1):
                        print(f'     {rank}. {stock.get("stock_name", "N/A")} - {stock.get("value", 0):.2f}')
    except Exception as e:
        print(f'❌ 失败: {e}')

    # 7. Quant PE/PB走势
    print('\n📊 7. Quant PE/PB 走势分析')
    print('-' * 80)
    try:
        result = parse_result(client.call_tool('quant', 'common_trend_get', {'stock_code': 'SZ000776', 'cycle': '1y'}))
        if 'data' in result and isinstance(result['data'], list):
            latest = result['data'][-1]
            # 获取值并处理None情况
            pettm = latest.get("pettm", 0)
            pettm = pettm if pettm is not None else 0
            pettm_percent = latest.get("pettm_percent", 0)
            pettm_percent = pettm_percent if pettm_percent is not None else 0
            pb = latest.get("pb", 0)
            pb = pb if pb is not None else 0
            pb_percent = latest.get("pb_percent", 0)
            pb_percent = pb_percent if pb_percent is not None else 0

            print(f'✅ PE/PB 走势分析 (最近数据)：')
            print(f'   股票: SZ000776 (广发证券)')
            print(f'   时间: {latest.get("trade_date", "N/A")}')
            print(f'   市盈率PE: {pettm:.2f} (百分位: {pettm_percent:.2f}%)')
            print(f'   市净率PB: {pb:.2f} (百分位: {pb_percent:.2f}%)')
    except Exception as e:
        print(f'❌ 失败: {e}')

    print('\n' + '=' * 80)
    print('🎉 脚本功能演示完成！')
    print('=' * 80)
    print('\n📝 可用的脚本命令行工具总结:')
    print('  LHB 龙虎榜:     SPK:lhb daily <date> <market>')
    print('  LHB 概括:       SPK:lhb outline [plate]')
    print('  LHB 排行:       SPK:lhb top [months] [page] [size]')
    print('\n  Windmill 指数:  SPK:windmill list [page] [size]')
    print('\n  ETF 涨幅榜:     SPK:etf rise [page] [size]')
    print('  ETF 跌幅榜:     SPK:etf fall [page] [size]')
    print('  ETF 主力资金:   SPK:etf fund [page] [size]')
    print('  ETF 换手榜:     SPK:etf feature [page] [size]')
    print('  ETF 关注榜:     SPK:etf hot [page] [size]')
    print('\n  Quant 基本指标: SPK:quant basic <code1> [code2]')
    print('  Quant 对比:     SPK:quant compare <code1> <code2> [year]')
    print('  Quant 行业:     SPK:quant industry <code1> [code2]')
    print('\n' + '=' * 80)

if __name__ == "__main__":
    demo_all_services()
