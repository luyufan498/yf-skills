"""test_storage.py - 存储模块单元测试"""

import tempfile
import pytest
from pathlib import Path
from paper_trading.storage import JsonStorage
from paper_trading.models import Account, CapitalPool


def test_storage_uses_tradings_directory():
    """测试存储模块使用 tradings 目录"""
    # 创建临时目录作为工作空间
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.config import get_workspace_config
        config = get_workspace_config()

        # 验证 tradings_dir 配置
        assert config['tradings_dir'].name == 'tradings'
        assert config['tradings_dir'].parent == Path(tmpdir)

        # 创建存储实例
        storage = JsonStorage()

        # 验证存储实例使用 tradings 目录
        assert storage.base_dir == config['tradings_dir']

        # 创建测试账户
        account = Account(
            stock_name="测试股票",
            capital_pool=CapitalPool(total=100000, available=100000)
        )

        # 保存账户
        saved_path = storage.save_account(account)

        # 验证文件保存在 tradings 目录下
        assert 'tradings' in str(saved_path)
        assert '测试股票' in str(saved_path)
        # 验证不使用 intermediate 目录
        assert 'intermediate' not in str(saved_path)


def test_storage_account_directory_structure():
    """测试账户目录结构为 tradings/clean_name"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        storage = JsonStorage()

        # 测试普通股票名称
        account_dir = storage._get_account_dir('赛力斯')
        expected = Path(tmpdir) / 'tradings' / '赛力斯'
        assert account_dir == expected

        # 测试带特殊字符的股票名称（不做清理）
        account_dir = storage._get_account_dir('ST+测试')
        expected = Path(tmpdir) / 'tradings' / 'ST+测试'
        assert account_dir == expected


def test_storage_preserves_account_files():
    """测试存储功能保持原有能力"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        storage = JsonStorage()
        account = Account(
            stock_name="测试股",
            capital_pool=CapitalPool(total=100000, available=100000)
        )

        # 保存账户
        saved_path = storage.save_account(account)
        assert saved_path.exists()

        # 加载账户
        loaded_account = storage.load_account("测试股")
        assert loaded_account is not None
        assert loaded_account.stock_name == "测试股"

        # 列出账户
        accounts = storage.list_accounts()
        assert "测试股" in accounts
