"""执行层开关（Phase 2：系统兜底单）——paper_trading_v2 与 watch_scan 共用的配置契约。

配置真源：``<workspace>/.paper-trading/exec_layer.json``（**缺文件 = 全关**，零影响）

    {
      "protect_orders": {
        "mode": "off",               // off | shadow | orders
        "exec_stocks": ["中芯国际"]   // 仅 mode=orders 生效：逐票白名单（名单外只留痕）
      }
    }

语义（2026-09-10 用户拍板）：

- ``off``    : 不生成系统兜底单；扫描侧对历史 ``protect:*`` 槽**也只留痕不执行**。
- ``shadow`` : 生成兜底单 + 只留痕（影子期 ≥3 个交易日对账），**零 ptrade2 调用**。
- ``orders`` : 生成；**仅 ``exec_stocks`` 内的标的**同拍直调执行，名单外仍只留痕。
               —— 禁止一次性全局翻转：逐票切换、逐票可回滚（动的是护仓活线路）。

逃生阀（测试/排障）：``PTRADE2_EXEC_LAYER_FILE`` 指定配置文件路径；
``PTRADE2_PROTECT_ORDERS`` 直接覆盖 mode。
"""
import json
import os

DEFAULT_MODE = 'off'
VALID_MODES = ('off', 'shadow', 'orders')
DEFAULT_WS_ROOT = '/home/catmouse/Github_Project/daily-stock-workspace'
CONFIG_BASENAME = 'exec_layer.json'


def config_path() -> str:
    """配置文件路径（env 逃生阀优先；缺省=<workspace>/exec_layer.json）。

    workspace 解析与 ``config.get_workspace_config()`` 同源：``STOCK_ANALYSIS_WORKSPACE``
    （= 生产上的 ``.../daily-stock-workspace/.paper-trading``，master_pool.db 所在目录）；
    env 缺失时才退到 ``STOCK_ANALYSIS_WORKSPACE_ROOT`` + ``.paper-trading``。
    """
    p = os.environ.get('PTRADE2_EXEC_LAYER_FILE')
    if p:
        return p
    ws = os.environ.get('STOCK_ANALYSIS_WORKSPACE')
    if ws:
        return os.path.join(ws, CONFIG_BASENAME)
    root = os.environ.get('STOCK_ANALYSIS_WORKSPACE_ROOT', DEFAULT_WS_ROOT)
    return os.path.join(root, '.paper-trading', CONFIG_BASENAME)


def load_config() -> dict:
    """读配置；文件缺失/不是合法 JSON/非对象 → {}（= 全关，fail-closed）。"""
    try:
        with open(config_path(), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _mode_for(key: str, stock_name: str | None = None, env_var: str | None = None) -> str:
    """通用口径读取（v14/Phase 3 抽出）：env 逃生阀 > 配置 > off，逐票白名单降级 shadow。"""
    env = (os.environ.get(env_var) or '').strip().lower() if env_var else ''
    cfg = load_config().get(key)
    cfg = cfg if isinstance(cfg, dict) else {}
    mode = env or str(cfg.get('mode') or DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE
    if mode != 'orders' or not stock_name:
        return mode
    wl = cfg.get('exec_stocks')
    wl = wl if isinstance(wl, (list, tuple)) else []
    names = {str(x).strip() for x in wl if str(x).strip()}
    return 'orders' if stock_name in names else 'shadow'


def protect_mode(stock_name: str | None = None) -> str:
    """返回该标的的兜底单口径：``off`` / ``shadow`` / ``orders``。

    ``mode=orders`` 且给了 stock_name 时，只有白名单内 → ``orders``，否则降级 ``shadow``
    （名单外只留痕）——这是"逐票切换、禁止全局翻转"的落地点。
    """
    return _mode_for('protect_orders', stock_name, 'PTRADE2_PROTECT_ORDERS')


def tp_mode(stock_name: str | None = None) -> str:
    """返回该标的的**止盈挂单**口径：``off`` / ``shadow`` / ``orders``（Phase 3，2026-09-10）。

    与 ``protect_mode`` 完全同契约，只是读 ``tp_orders`` 段（env 逃生阀
    ``PTRADE2_TP_ORDERS``）。止盈与兜底单**分开开关**的理由：两者风险不同向——
    兜底单错触发=少赚/早卖（机会成本），保护单漏触发=多亏本金；分开才能分别切、分别回滚。
    """
    return _mode_for('tp_orders', stock_name, 'PTRADE2_TP_ORDERS')
