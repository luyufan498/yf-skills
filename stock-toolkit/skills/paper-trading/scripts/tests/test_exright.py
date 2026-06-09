"""Test cases for ExRight handling"""

import pytest
import tempfile
import shutil
from datetime import datetime

from paper_trading.models import (
    Account,
    CapitalPool,
    Position,
    OperationType,
    ExRightAppliedRecord,
)
from paper_trading.exright_fetcher import ExRightFetcher
from paper_trading.exright_cache import ExRightCache
from paper_trading.exright_handler import ExRightHandler
from paper_trading.trading import PaperTrader


class TestExRightFetcher:
    """Test ExRightFetcher"""

    def test_parse_fhcontent_bonus_only(self):
        """测试纯分红方案"""
        bonus, split = ExRightFetcher._parse_fhcontent("10派308.76元")
        assert bonus == 308.76
        assert split == 0.0

    def test_parse_fhcontent_bonus_and_split(self):
        """测试分红送转方案"""
        bonus, split = ExRightFetcher._parse_fhcontent("10派1.7元转3股")
        assert bonus == 1.7
        assert split == 3.0

    def test_parse_fhcontent_give_and_bonus(self):
        """测试送股加分红方案"""
        bonus, split = ExRightFetcher._parse_fhcontent("10送5派2元")
        assert bonus == 2.0
        assert split == 5.0

    def test_parse_fhcontent_split_first(self):
        """测试送转在前分红在后的方案"""
        bonus, split = ExRightFetcher._parse_fhcontent("10转3股派1.7元")
        assert bonus == 1.7
        assert split == 3.0

    def test_parse_fhcontent_no_bonus(self):
        """测试无分红方案"""
        bonus, split = ExRightFetcher._parse_fhcontent("10转3股")
        assert bonus == 0.0
        assert split == 3.0


class TestExRightCache:
    """Test ExRightCache"""

    @pytest.fixture
    def temp_cache_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_cache_ttl(self, temp_cache_dir):
        """测试缓存 TTL 过期"""
        cache = ExRightCache(cache_dir=temp_cache_dir)

        # 写入昨天缓存
        yesterday = "2024-01-01"
        cache_path = cache._get_cache_path("sh000001")
        import json
        with open(cache_path, 'w') as f:
            json.dump({
                "stock_code": "sh000001",
                "cached_at": f"{yesterday}T10:00:00",
                "events": [{"cqr": "2024-01-01"}]
            }, f)

        # 今天读应该过期
        result = cache.get("sh000001")
        assert result is None

    def test_cache_today_valid(self, temp_cache_dir):
        """测试当天缓存有效"""
        cache = ExRightCache(cache_dir=temp_cache_dir)

        today = datetime.now().strftime('%Y-%m-%d')
        cache_path = cache._get_cache_path("sh000001")
        import json
        with open(cache_path, 'w') as f:
            json.dump({
                "stock_code": "sh000001",
                "cached_at": f"{today}T10:00:00",
                "events": [{"cqr": "2024-06-01"}]
            }, f)

        result = cache.get("sh000001")
        assert result is not None
        assert result['events'][0]['cqr'] == "2024-06-01"


class TestExRightHandler:
    """Test ExRightHandler core logic"""

    @pytest.fixture
    def temp_storage(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def trader(self, temp_storage):
        from paper_trading.storage import StorageFactory
        storage = StorageFactory.create_storage("json", base_dir=temp_storage)
        return PaperTrader(storage=storage)

    def test_get_position_qty_at_date_with_buy(self, trader):
        """测试登记日持仓判断 - 买入后未卖出"""
        account = Account(
            stock_name="测试股票",
            stock_code="sh000001",
            capital_pool=CapitalPool(total=100000, available=100000, used=0),
            positions=[
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=50.0,
                    total_cost=50000.0,
                    operation=OperationType.BUY,
                    timestamp="2024-01-01T10:00:00"
                )
            ]
        )

        handler = ExRightHandler(trader)
        assert handler._get_position_qty_at_date(account, "2024-06-01") == 1000

    def test_get_position_qty_at_date_with_sell(self, trader):
        """测试登记日持仓判断 - 买入后已清仓"""
        account = Account(
            stock_name="测试股票",
            stock_code="sh000001",
            capital_pool=CapitalPool(total=100000, available=100000, used=0),
            positions=[
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=50.0,
                    total_cost=50000.0,
                    operation=OperationType.BUY,
                    timestamp="2024-01-01T10:00:00"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=60.0,
                    total_cost=50000.0,
                    operation=OperationType.SELL,
                    timestamp="2024-05-01T10:00:00"
                )
            ]
        )

        handler = ExRightHandler(trader)
        assert handler._get_position_qty_at_date(account, "2024-06-01") == 0

    def test_get_remaining_position_with_exright(self, trader):
        """测试 FIFO 引擎感知除权后的持仓计算"""
        account = Account(
            stock_name="测试股票",
            stock_code="sh000001",
            capital_pool=CapitalPool(total=100000, available=100000, used=0),
            positions=[
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=94.04,
                    total_cost=94040.0,
                    operation=OperationType.BUY,
                    timestamp="2024-01-01T10:00:00"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=200,
                    price=94.77,
                    total_cost=18808.0,
                    operation=OperationType.SELL,
                    timestamp="2024-05-28T10:00:00"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=400,
                    price=89.23,
                    total_cost=37616.0,
                    operation=OperationType.SELL,
                    timestamp="2024-05-29T10:00:00"
                ),
                # 除权: 10转3派1.7
                Position(
                    stock_code="sh000001",
                    quantity=120,  # 400 * 0.3
                    price=0.0,
                    total_cost=0.0,
                    operation=OperationType.EXRIGHT_BONUS,
                    timestamp="2024-06-01T09:30:00",
                    note="除权送转: 10派1.7元转3股"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=0,
                    price=0.0,
                    total_cost=-68.0,  # 400 * 0.17
                    operation=OperationType.EXRIGHT_DIVIDEND,
                    timestamp="2024-06-01T09:30:00",
                    note="除权分红: 10派1.7元转3股"
                ),
            ]
        )

        qty, cost = trader.get_remaining_position(account)
        # 400 + 120 = 520
        assert qty == 520
        # 37616 - 68 = 37548
        assert abs(cost - 37548.0) < 1.0

    def test_fifo_with_sell_after_exright(self, trader):
        """测试除权后卖出的 FIFO 成本"""
        account = Account(
            stock_name="测试股票",
            stock_code="sh000001",
            capital_pool=CapitalPool(total=100000, available=100000, used=0),
            positions=[
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=94.04,
                    total_cost=94040.0,
                    operation=OperationType.BUY,
                    timestamp="2024-01-01T10:00:00"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=200,
                    price=94.77,
                    total_cost=18808.0,
                    operation=OperationType.SELL,
                    timestamp="2024-05-28T10:00:00"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=400,
                    price=89.23,
                    total_cost=37616.0,
                    operation=OperationType.SELL,
                    timestamp="2024-05-29T10:00:00"
                ),
                # 除权: 10转3派1.7
                Position(
                    stock_code="sh000001",
                    quantity=120,
                    price=0.0,
                    total_cost=0.0,
                    operation=OperationType.EXRIGHT_BONUS,
                    timestamp="2024-06-01T09:30:00",
                    note="除权送转: 10派1.7元转3股"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=0,
                    price=0.0,
                    total_cost=-68.0,
                    operation=OperationType.EXRIGHT_DIVIDEND,
                    timestamp="2024-06-01T09:30:00",
                    note="除权分红: 10派1.7元转3股"
                ),
                # 除权后卖出 200 股
                Position(
                    stock_code="sh000001",
                    quantity=200,
                    price=66.66,
                    total_cost=14442.0,  # 200 * 72.21
                    operation=OperationType.SELL,
                    timestamp="2024-06-01T14:00:00"
                ),
            ]
        )

        qty, cost = trader.get_remaining_position(account)
        # 520 - 200 = 320
        assert qty == 320
        # (37548 - 14442) = 23106
        assert abs(cost - 23106.0) < 1.0

    def test_clear_all_exright_on_empty_position(self, trader):
        """测试清仓后除权记录不影响新持仓"""
        account = Account(
            stock_name="测试股票",
            stock_code="sh000001",
            capital_pool=CapitalPool(total=100000, available=100000, used=0),
            positions=[
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=50.0,
                    total_cost=50000.0,
                    operation=OperationType.BUY,
                    timestamp="2024-01-01T10:00:00"
                ),
                Position(
                    stock_code="sh000001",
                    quantity=1000,
                    price=60.0,
                    total_cost=50000.0,
                    operation=OperationType.SELL,
                    timestamp="2024-03-01T10:00:00"
                ),
                # 除权发生在清仓后
                Position(
                    stock_code="sh000001",
                    quantity=120,
                    price=0.0,
                    total_cost=0.0,
                    operation=OperationType.EXRIGHT_BONUS,
                    timestamp="2024-06-01T09:30:00"
                ),
            ]
        )

        qty, cost = trader.get_remaining_position(account)
        assert qty == 0
        assert cost == 0.0
