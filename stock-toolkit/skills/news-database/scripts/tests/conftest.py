"""共享 fixtures。"""

import pytest


@pytest.fixture
def db_path(tmp_path):
    """每个测试独立的临时数据库路径。"""
    return tmp_path / "test.db"
