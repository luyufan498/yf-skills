"""测试 fixture：临时 workspace，不碰真实数据"""
import pytest
import os
from pathlib import Path

@pytest.fixture
def ws(tmp_path):
    """临时 workspace 根，模拟 STOCK_ANALYSIS_WORKSPACE"""
    os.environ['STOCK_ANALYSIS_WORKSPACE'] = str(tmp_path)
    return tmp_path

@pytest.fixture
def db_path(ws):
    """SQLite 数据库路径"""
    return ws / 'master_pool.db'
