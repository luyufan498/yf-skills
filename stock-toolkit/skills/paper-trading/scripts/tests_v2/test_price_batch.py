"""P1 批量实时价 CLI（fetch-prices）：mock fetch_batch，零真实网络。

跑法：scripts/.venv/bin/python -m pytest tests_v2/test_price_batch.py -q
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from typer.testing import CliRunner
from unittest.mock import patch

runner = CliRunner()

from paper_trading_v2.models import StockInfo, MarketType


def _info(code, name, price, pre=10.0, vol="12345"):
    return StockInfo(
        code=code, name=name, market=MarketType.A_SHARE,
        current_price=price, pre_close=pre, open_price=price - 0.1,
        high=price + 0.5, low=price - 0.5, volume=vol,
        date="2026-09-04", time="15:00:03", source="tencent")


def _fake_batch(codes):
    out = {}
    for c in codes:
        if c in ('sh688041', 'sz002536', 'hk00700'):
            out[c] = _info(c, f'股票{c[-5:]}', 100.0 + len(c))
    return out  # 未匹配的 code 自然缺席 → 容错路径


def _run(args, batch_side=_fake_batch):
    from paper_trading_v2.cli import app
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.fetch_batch',
               side_effect=batch_side):
        return runner.invoke(app, args)


def test_multi_codes_json(ws):
    """多 code：json 数组字段完整、按输入顺序"""
    r = _run(["fetch-prices", "sh688041,sz002536,hk00700", "--format", "json"])
    assert r.exit_code == 0, r.output
    items = json.loads(r.output)
    assert [i['code'] for i in items] == ['sh688041', 'sz002536', 'hk00700']
    for i in items:
        for key in ('code', 'name', 'current_price', 'pre_close', 'open',
                    'high', 'low', 'volume', 'time'):
            assert key in i, f"缺字段 {key}: {i}"


def test_single_code_json(ws):
    """单 code 兼容（fetch-price 的批量等价）"""
    r = _run(["fetch-prices", "sh688041", "--format", "json"])
    assert r.exit_code == 0, r.output
    items = json.loads(r.output)
    assert len(items) == 1 and items[0]['code'] == 'sh688041'


def test_invalid_code_skipped(ws):
    """空/非法 code 容错：跳过不崩，好 code 照常返回"""
    r = _run(["fetch-prices", "sh688041,,  ,badcode123", "--format", "json"])
    assert r.exit_code == 0, r.output
    items = json.loads(r.output)
    assert [i['code'] for i in items] == ['sh688041']


def test_all_missed_exits_1(ws):
    """全部未取到：exit 1（fail-closed，不输出假数据）"""
    r = _run(["fetch-prices", "sh688041"], batch_side=lambda codes: {})
    assert r.exit_code == 1
    assert "未取到" in r.output


def test_pretty_format(ws):
    """pretty：每只一行 + miss 提示"""
    r = _run(["fetch-prices", "sh688041,sz002536,xx999"])
    assert r.exit_code == 0, r.output
    assert "批量实时价（2 只）" in r.output
    assert "sh688041" in r.output and "sz002536" in r.output
    assert "未取到: xx999" in r.output


def test_only_valid_codes_passed_to_fetch_batch(ws):
    """传给 fetch_batch 的列表已过滤空串/空白（不把垃圾塞给网络层）"""
    seen = {}

    def spy(codes):
        seen['codes'] = list(codes)
        return _fake_batch(codes)

    r = _run(["fetch-prices", " sh688041 , ,,sz002536,"], batch_side=spy)
    assert r.exit_code == 0, r.output
    assert seen['codes'] == ['sh688041', 'sz002536']


if __name__ == '__main__':
    # 直跑（ws 语义 = STOCK_ANALYSIS_WORKSPACE 指 tmp，本组测试不落盘，env 仅为守规矩）
    import tempfile
    os.environ['STOCK_ANALYSIS_WORKSPACE'] = tempfile.mkdtemp(prefix='price_batch_')
    fns = [test_multi_codes_json, test_single_code_json, test_invalid_code_skipped,
           test_all_missed_exits_1, test_pretty_format,
           test_only_valid_codes_passed_to_fetch_batch]
    fails = 0
    for fn in fns:
        try:
            fn(tempfile.mkdtemp(prefix='ws_'))
            print(f"✅ {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"❌ {fn.__name__}: {e}")
    raise SystemExit(1 if fails else 0)
