"""test_config.py - 配置模块单元测试"""

import os
from pathlib import Path
import pytest
from paper_trading.config import get_workspace_config, get_trading_account_dir, get_stock_analysis_dir, get_stock_temp_data_dir

# 清除可能设置的环境变量，确保测试隔离
@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch):
    monkeypatch.delenv('STOCK_ANALYSIS_WORKSPACE', raising=False)
    monkeypatch.delenv('STOCK_INTERMEDIATE_DIR', raising=False)
    yield

def test_get_workspace_config_defaults():
    """测试默认配置路径"""
    config = get_workspace_config()

    # 验证返回的路径对象
    assert 'workspace_root' in config
    assert 'tradings_dir' in config
    assert 'stocks_analysis_dir' in config

    # 验证 tradings_dir 路径
    assert config['tradings_dir'].name == 'tradings'

    # 验证 stocks_analysis_dir 路径
    assert config['stocks_analysis_dir'].name == 'stocks_analysis'
    assert config['stocks_analysis_dir'].parent == config['workspace_root']

def test_get_workspace_config_with_workspace_env(monkeypatch):
    """测试使用 STOCK_ANALYSIS_WORKSPACE 环境变量"""
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', '/custom/workspace')

    config = get_workspace_config()

    assert config['workspace_root'] == Path('/custom/workspace')
    assert config['tradings_dir'] == Path('/custom/workspace/tradings')

def test_get_trading_account_dir():
    """测试获取交易账户目录路径"""
    config = get_workspace_config()

    account_dir = get_trading_account_dir('赛力斯')
    expected_path = config['tradings_dir'] / '赛力斯'
    assert account_dir == expected_path

def test_get_trading_account_dir_with_special_chars():
    """测试特殊字符的股票名称处理"""
    config = get_workspace_config()

    account_dir = get_trading_account_dir('ST+测试股')
    # 应该直接使用原始名称，不做清理
    assert account_dir == config['tradings_dir'] / 'ST+测试股'

def test_get_stock_analysis_dir():
    """测试获取股票分析目录路径"""
    config = get_workspace_config()

    analysis_dir = get_stock_analysis_dir('比亚迪')
    expected_path = config['stocks_analysis_dir'] / '比亚迪'
    assert analysis_dir == expected_path

def test_path_functions_are_absolute():
    """测试所有路径函数返回绝对路径"""
    config = get_workspace_config()

    assert config['workspace_root'].is_absolute()
    assert config['tradings_dir'].is_absolute()
    assert config['stocks_analysis_dir'].is_absolute()
    assert get_trading_account_dir('test').is_absolute()
    assert get_stock_analysis_dir('test').is_absolute()

def test_get_temp_data_dir():
    """测试获取 temp-data 目录配置"""
    from paper_trading.config import get_workspace_config

    config = get_workspace_config()
    assert 'temp_data_dir' in config
    assert config['temp_data_dir'].parts[-1] == 'temp-data'
    assert config['temp_data_dir'].parent == config['workspace_root']


def test_get_stock_temp_data_dir():
    """测试获取股票临时数据目录路径"""
    # 不带 category
    result = get_stock_temp_data_dir("测试股票")
    assert result.name == "测试股票"
    assert result.parent.name == "temp-data"

    # 带 category
    result = get_stock_temp_data_dir("测试股票", "deep-search")
    assert result.name == "deep-search"
    assert result.parent.name == "测试股票"
    assert result.parent.parent.name == "temp-data"

