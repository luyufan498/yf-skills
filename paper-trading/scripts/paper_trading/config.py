"""配置管理"""

from pathlib import Path


def get_workspace_config() -> dict:
    """获取工作空间配置

    优先使用环境变量，否则默认为当前目录
    """
    import os

    # 获取脚本所在目录的父目录（SKILL_ROOT）
    SKILL_ROOT = Path(__file__).parent.parent

    # 工作空间根目录（优先使用环境变量）
    WORKSPACE_ROOT = Path(os.getenv('STOCK_ANALYSIS_WORKSPACE', SKILL_ROOT))

    # 中间文件目录（优先使用环境变量）
    INTERMEDIATE_DIR = Path(os.getenv('STOCK_INTERMEDIATE_DIR', WORKSPACE_ROOT / "intermediate"))

    return {
        'skill_root': SKILL_ROOT,
        'workspace': WORKSPACE_ROOT,
        'intermediate': INTERMEDIATE_DIR,
    }
