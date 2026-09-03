"""测试 fixture：临时 workspace，不碰真实数据"""
import pytest
import os
from pathlib import Path

@pytest.fixture(autouse=True)
def isolate_tasks_db(tmp_path, monkeypatch):
    """红线护栏：测试绝不触碰生产 tasks.db——STOCK_TASKS_DB 一律指向 tmp 副本
    （sleeve-order-expire 自 v12-patch/E2 起会写 MSG_REJUDGE 到 tasks.db；
    测试显式 monkeypatch.setenv 可覆盖，仍在本测试 tmp_path 域内）。"""
    monkeypatch.setenv('STOCK_TASKS_DB', str(tmp_path / 'tasks.db'))

@pytest.fixture
def ws(tmp_path):
    """临时 workspace 根，模拟 STOCK_ANALYSIS_WORKSPACE"""
    os.environ['STOCK_ANALYSIS_WORKSPACE'] = str(tmp_path)
    return tmp_path

@pytest.fixture
def db_path(ws):
    """SQLite 数据库路径"""
    return ws / 'master_pool.db'
