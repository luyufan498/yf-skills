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


@pytest.fixture(autouse=True)
def _disable_code_name_gate(monkeypatch):
    """测试默认关闭「代码↔名称一致性闸门」（_ensure_code，2026-09-09 北方铜业事故）。

    合成名称（NEWS票/收编票…）配真实代码必然对不上，且校验器会触网 → 让用例
    随网络抖动 flaky（test_sleeve_cli 曾因此偶发失败）。需要测闸门的用例自行
    `monkeypatch.delenv('PTRADE2_ALLOW_CODE_MISMATCH', raising=False)` 打开。
    """
    monkeypatch.setenv('PTRADE2_ALLOW_CODE_MISMATCH', '1')

@pytest.fixture
def ws(tmp_path):
    """临时 workspace 根，模拟 STOCK_ANALYSIS_WORKSPACE"""
    os.environ['STOCK_ANALYSIS_WORKSPACE'] = str(tmp_path)
    return tmp_path

@pytest.fixture
def db_path(ws):
    """SQLite 数据库路径"""
    return ws / 'master_pool.db'
