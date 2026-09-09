"""股票代码查询器

支持搜索A股、港股、美股的股票代码
"""

from typing import List, Dict, Optional
import os
import re

import requests


class StockValidationError(Exception):
    """股票名称验证失败异常"""
    pass


def validate_stock_name(stock_name: str) -> tuple[bool, Optional[str]]:
    """
    验证股票名称是否合法

    使用 StockCodeSearcher 查询股票名称是否存在

    Args:
        stock_name: 股票名称

    Returns:
        (是否合法, 股票代码) 元组，如果不合法则代码为 None
    """
    if not stock_name or not stock_name.strip():
        return False, None

    searcher = StockCodeSearcher()
    results = searcher.search_cn_stocks(stock_name, limit=1)

    if results:
        # 找到匹配的股票，返回 True 和股票代码
        return True, results[0]['code']
    # 2026-09-01 本地兜底：新浪 suggest 名称搜索服务端失效（名称一律不命中、仅代码可查），
    # 用本地代码映射兜底，避免 validate 卡死分析报告保存链路（荣盛石化 2026-09-01 实测）。
    fallback = _LOCAL_CODE_FALLBACK.get(stock_name)
    if fallback:
        return True, fallback
    return False, None


# 本地 A 股代码映射（仅兜底用；优先走新浪 suggest，命中即不走此表）
_LOCAL_CODE_FALLBACK = {
    '高澜股份': 'sz300499',
    '飞龙股份': 'sz002536',
    '领益智造': 'sz002600',
    '雅克科技': 'sz002409',
    '申菱环境': 'sz301018',
    '阳光电源': 'sz300274',
    '寒武纪': 'sh688256',
    '澜起科技': 'sh688008',
    '长鑫科技': 'sh688825',
    '江波龙': 'sz301308',
    '佰维存储': 'sh688525',
    '荣盛石化': 'sz002493',
    '卫星化学': 'sz002648',
    '国际复材': 'sz301526',
    '中国巨石': 'sh600176',
    '中际旭创': 'sz300308',
    '中科曙光': 'sh603019',
    '中芯国际': 'sh688981',
    '分众传媒': 'sz002027',
    '北斗星通': 'sz002151',
    '凯莱英': 'sz002821',
    '工业富联': 'sh601138',
    '恒申新材': 'sz000782',
    '恒瑞医药': 'sh600276',
    '源杰科技': 'sh688498',
    '中航沈飞': 'sh600760',
    '航发动力': 'sh600893',
    '爱司凯': 'sz300521',
    '赣锋锂业': 'sz002460',
    '中国海油': 'sh600938',
    '中国石化': 'sh600028',
    '中国石油': 'sh601857',
    '钢研高纳': 'sz300034',
    '长飞光纤': 'sh601869',
    '国电南瑞': 'sh600406',
    '兆易创新': 'sh603986',
    '天孚通信': 'sz300394',
    '有研硅': 'sh688432',
    '英维克': 'sz002837',
    '盛合晶微': 'sh688820',
    '科创新源': 'sz300731',
    '生益科技': 'sh600183',
    '盛科通信': 'sh688702',
    '北方华创': 'sz002371',
    '中微公司': 'sh688012',
    '拓荆科技': 'sh688072',
    '罗博特科': 'sz300757',
    '亚康股份': 'sz301085',
    '中信证券': 'sh600030',
    '本川智能': 'sz300964',
    '奥来德': 'sh688378',
    '北方铜业': 'sz000737',
    '龙版传媒': 'sh605577',
    '中海油服': 'sh601808',
    '中船特气': 'sh688146',
    '中远海能': 'sh600026',
    '北方稀土': 'sh600111',
    '星网锐捷': 'sz002396',
    '潍柴动力': 'sz000338',
    '金力永磁': 'sz300748',
    '铂科新材': 'sz300811',
    '三安光电': 'sh600703',
    '东方盛虹': 'sz000301',
    '中兴通讯': 'sz000063',
    '中恒电气': 'sz002364',
    '恒力石化': 'sh600346',
    '新易盛': 'sz300502',
    '海光信息': 'sh688041',
    '高新发展': 'sz000628',
}


class StockCodeSearcher:
    """股票代码查询器"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }

        # 常用港股/美股代码库
        self.hot_stocks = {
            'hk': {
                '腾讯控股': 'hk00700',
                '中国移动': 'hk00941',
                '阿里巴巴-SW': 'hk09988',
                '美团-W': 'hk03690',
                '小米集团-W': 'hk01810',
                '比亚迪股份': 'hk01211',
                '京东集团-SW': 'hk09618',
                '网易-S': 'hk09999',
                '中芯国际': 'hk00981',
                '工商银行': 'hk01398',
                '建设银行': 'hk00939',
                '中国银行': 'hk03988',
                '招商银行': 'hk03968',
                '中国平安': 'hk02318',
                '中国石油股份': 'hk00857',
                '汇丰控股': 'hk00005',
                '长江实业': 'hk01113',
            },
            'gb': {
                '苹果': 'gb_aapl',
                '谷歌': 'gb_goog',
                '谷歌A类股': 'gb_googl',
                '微软': 'gb_msft',
                '亚马逊': 'gb_amzn',
                '特斯拉': 'gb_tsla',
                '英伟达': 'gb_nvda',
                'Meta(FB)': 'gb_meta',
                '伯克希尔-哈撒韦': 'gb_brk_a',
                '强生': 'gb_jnj',
                '可口可乐': 'gb_ko',
                '麦当劳': 'gb_mcd',
                '迪士尼': 'gb_dis',
                '耐克': 'gb_nke',
            }
        }

    def search_cn_stocks(self, keyword: str, limit: int = 20) -> List[dict]:
        """
        搜索A股、港股、美股股票代码

        Args:
            keyword: 搜索关键词（股票名称或代码）
            limit: 返回结果数量

        Returns:
            股票列表
        """
        # 使用新浪财经suggest API
        # type=11:A股, 12:港股, 13:美股, 14:概念
        # 2026-09-01 修复：新浪 suggest 接口现要求 GBK 编码 key（UTF-8 一律返回空），响应为 GBK 编码
        from urllib.parse import quote
        encoded_key = quote(str(keyword).encode('gbk', errors='ignore'))
        url = f"https://suggest3.sinajs.cn/suggest/type=11,12,13&key={encoded_key}&name=suggestdata"

        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            content = response.content.decode('gbk', errors='ignore')

            if 'suggestdata="' in content:
                data_str = content.split('suggestdata="')[1].split('";')[0]

                results = []
                kw = str(keyword).strip()
                for item in data_str.split(';'):
                    if not item:
                        continue

                    parts = item.split(',')
                    if len(parts) >= 5:
                        name = parts[0]
                        stock_type = parts[1]
                        code = parts[2]
                        full_code = parts[3]

                        # 2026-09-01 相关性过滤：新浪接口名称搜不到时返回50条默认热门股兜底列表
                        # （与搜索词无关），直接取 results[0] 会错配成 sh000001 上证指数。
                        # 仅保留名称含关键词或代码匹配的结果，全不匹配则返回空（交给调用方兜底）。
                        if kw and not (
                            kw in name
                            or kw in full_code
                            or kw == code
                            or kw in code
                            or kw in stock_type
                        ):
                            continue

                        type_map = {'11': 'A股', '12': '港股', '13': '美股'}
                        market = type_map.get(stock_type, '其他')

                        if full_code:
                            formatted_code = full_code.lower()
                        elif len(code) == 6:
                            if code.startswith('6'):
                                formatted_code = f'sh{code}'
                            elif code.startswith(('0', '3')):
                                formatted_code = f'sz{code}'
                            elif code.startswith('8'):
                                formatted_code = f' bj{code}'
                            else:
                                formatted_code = code
                        else:
                            formatted_code = code

                        results.append({
                            'name': name,
                            'code': formatted_code,
                            'original_code': code,
                            'full_code': full_code,
                            'market': market,
                            'source': '新浪财经'
                        })

                return results[:limit]

            return []

        except Exception as e:
            print(f"Error searching stocks: {e}")
            return []

    def search_hot_stocks(self, keyword: str, limit: int = 20) -> List[dict]:
        """
        在常用股票库中搜索（港股、美股）

        Args:
            keyword: 搜索关键词
            limit: 返回结果数量

        Returns:
            股票列表
        """
        keyword = keyword.lower()
        results = []

        for name, code in self.hot_stocks['hk'].items():
            if keyword.lower() in name.lower() or keyword in code.lower():
                results.append({
                    'name': name,
                    'code': code,
                    'market': '港股',
                    'source': '内置数据库'
                })

        for name, code in self.hot_stocks['gb'].items():
            if keyword.lower() in name.lower() or keyword in code.lower():
                results.append({
                    'name': name,
                    'code': code,
                    'market': '美股',
                    'source': '内置数据库'
                })

        return results[:limit]

    def search(self, keyword: str, limit: int = 20) -> Dict[str, List[dict]]:
        """
        综合搜索股票代码

        Args:
            keyword: 搜索关键词
            limit: 每个市场的返回数量

        Returns:
            按市场分类的搜索结果
        """
        results = {
            'A_share': self.search_cn_stocks(keyword, limit),
            'hot_funds': self.search_hot_stocks(keyword, limit)
        }

        return results


# ============ code ↔ 中文名 归一（2026-09-07 亚康股份/301085 实体分裂事故修复） ============
# pool.stock 是 PRIMARY KEY、一只票存中文名：watchlist-add 纯代码入参先反查中文名，反查不到
# fail-closed 拒绝；ptrade2 canon-code 命令同源复用。newsdb 侧后续可直接复用 canonical_stock_code。

_DEFAULT_NEWS_DB = '/home/catmouse/Github_Project/daily-stock-workspace/data/news/news.db'

# 代码形态判定：可选 sh/sz/hk/bj/gb_ 前缀 + 数字/字母/点/连字符，不含中文字符与空格
# （中文名一律 False；'600176.SH' 后缀形态、'AAPL' 裸 ticker 均算代码形态）
_STOCK_CODE_RE = re.compile(r'(?:(?:sh|sz|bj|hk|gb)_?)?[a-z0-9._\-]+', re.IGNORECASE)

# 裸 6 位 A 股前缀推断（不保证正确，配合 resolve_name_for_code 校验；8/4/9=北交所）
_A_SHARE_PREFIX_INFER = {'6': 'sh', '0': 'sz', '3': 'sz', '8': 'bj', '4': 'bj', '9': 'bj'}

_GB_TICKER_CACHE = None


def _gb_ticker_set():
    """内置美股 gb 库的 ticker 集（懒加载；构造 StockCodeSearcher 不触网）"""
    global _GB_TICKER_CACHE
    if _GB_TICKER_CACHE is None:
        _GB_TICKER_CACHE = frozenset(
            code[len('gb_'):] for code in StockCodeSearcher().hot_stocks['gb'].values())
    return _GB_TICKER_CACHE


def looks_like_stock_code(raw) -> bool:
    """代码形态判定（watchlist-add 反查闸 + canon-code 路由）：可选 sh/sz/hk/bj/gb_ 前缀
    + 数字/字母/点/连字符。中文名（含中文字符或空格）一律 False。"""
    if raw is None:
        return False
    s = str(raw).strip()
    return bool(s) and _STOCK_CODE_RE.fullmatch(s) is not None


def canonical_stock_code(raw) -> str:
    """任意形态代码 → ptrade canonical 形态（sh600176/sz002493/hk00700/gb_aapl）。

    纯格式归一，不做网络请求；newsdb 侧后续直接复用。
    - 已带 sh/sz/bj/hk/gb_ 前缀（大小写随意）→ 小写化原样
    - '600176.SH'/'600176.sz' 后缀形态 → 前缀形态
    - 裸 6 位 A 股按首位推断前缀：6→sh；0/3→sz；8/4/9→bj（推断不保证正确，
      配合 resolve_name_for_code 以名字反推权威 code）
    - 裸 5 位 → hk（hk 前缀 4-5 位补齐 5 位）
    - 裸英文 ticker → 限内置 gb 库已有标的 → gb_<ticker>
    - 无法识别/中文输入 → ValueError（中文名不是 code，应传代码或用 watchlist-add 传中文名）
    """
    if raw is None:
        raise ValueError("股票代码不能为空")
    s = str(raw).strip().lower()
    if not s:
        raise ValueError("股票代码不能为空")
    if any('一' <= ch <= '鿿' for ch in s):
        raise ValueError(f"'{raw}' 是中文名称不是代码——请传代码（如 sz301085）或用 watchlist-add 传中文名")
    m = re.fullmatch(r'([0-9a-z]+)\.(sh|sz|bj|hk)', s)      # 后缀形态 600176.SH
    if m:
        s = m.group(2) + m.group(1)
    m = re.fullmatch(r'(sh|sz|bj)([0-9]{6})', s)            # A 股前缀形态
    if m:
        return m.group(1) + m.group(2)
    m = re.fullmatch(r'hk([0-9]{4,5})', s)                  # 港股（补齐 5 位）
    if m:
        return 'hk' + m.group(1).zfill(5)
    m = re.fullmatch(r'gb_?([a-z][a-z0-9_\-]*)', s)         # 美股
    if m:
        return 'gb_' + m.group(1)
    if re.fullmatch(r'[0-9]{6}', s):                        # 裸 6 位 A 股
        prefix = _A_SHARE_PREFIX_INFER.get(s[0])
        if prefix:
            return prefix + s
        raise ValueError(f"无法识别的 A 股代码 '{raw}'（首位 {s[0]} 不在 6/0/3/8/4/9 推断表内）")
    if re.fullmatch(r'[0-9]{5}', s):                        # 裸 5 位 → 港股
        return 'hk' + s
    if re.fullmatch(r'[a-z][a-z0-9_\-]*', s):               # 裸英文 ticker（限内置库）
        if s in _gb_ticker_set():
            return 'gb_' + s
        raise ValueError(f"无法识别的代码 '{raw}'——美股 ticker 仅支持内置库已有标的（如 aapl/tsla）")
    raise ValueError(f"无法解析 '{raw}' 为股票代码"
                     f"（支持 sh/sz/hk/gb_ 前缀、600176.SH 后缀、裸 6 位 A 股 / 5 位港股）")


def _news_db_path():
    """newsdb 路径：NEWS_DB_PATH 覆盖 > STOCK_NEWS_DB（newsdb CLI 现行约定）> 默认绝对路径"""
    return (os.environ.get('NEWS_DB_PATH')
            or os.environ.get('STOCK_NEWS_DB')
            or _DEFAULT_NEWS_DB)


def _code_key(code):
    """newsdb code 容错归一：'603019.SH'/'300731'/'sh600176' → '603019'/'300731'/'600176'"""
    s = str(code or '').strip().lower()
    for prefix in ('sh', 'sz', 'bj', 'hk'):
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix):]
            break
    for suffix in ('.sh', '.sz', '.bj', '.hk'):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            break
    return s


def _newsdb_name_for_code(canonical):
    """newsdb stocks 表（只读连接）code → name；连接失败/缺表/未命中 → None 不抛"""
    try:
        import sqlite3
        path = _news_db_path()
        if not path or not os.path.exists(path):
            return None
        key = _code_key(canonical)
        if not key:
            return None
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            rows = conn.execute('SELECT name, code FROM stocks').fetchall()
        finally:
            conn.close()
        for name, code in rows:
            if _code_key(code) == key:
                return name
    except Exception:
        return None
    return None


def _newsdb_code_for_name(name):
    """newsdb stocks 表（只读连接）name → code（原样返回，canonical 交给调用方）"""
    try:
        import sqlite3
        path = _news_db_path()
        if not path or not os.path.exists(path):
            return None
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            row = conn.execute('SELECT code FROM stocks WHERE name=? LIMIT 1', (name,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception:
        return None


def resolve_name_for_code(raw_code) -> Optional[str]:
    """代码（任意形态）→ 中文名；纯本地三级查找，不触网。

    a. _LOCAL_CODE_FALLBACK 值→键反查 → b. hot_stocks code→name 反查 → c. newsdb stocks
    表（mode=ro、sh 前缀/裸 6 位/.SH 后缀容错匹配）→ 找不到返回 None。
    非代码形态（含中文/乱码）canonical 抛 ValueError → 一律 None（fail-closed 由调用方拒绝）。
    """
    try:
        canonical = canonical_stock_code(raw_code)
    except ValueError:
        return None
    for name, code in _LOCAL_CODE_FALLBACK.items():
        if code == canonical:
            return name
    hot_reverse = {code: name for mkt in StockCodeSearcher().hot_stocks.values()
                   for name, code in mkt.items()}
    if canonical in hot_reverse:
        return hot_reverse[canonical]
    return _newsdb_name_for_code(canonical)


def lookup_code_for_name(name) -> Optional[str]:
    """中文名 → code（fallback / hot_stocks / newsdb 本地表，不触网）。

    canon-code 中文名路径与 watchlist-add 反查命中后的 code hint 复用；newsdb code 原样
    返回（'601127.SH' 等非 canonical 形态由调用方 canonical_stock_code 归一）。
    """
    if not name:
        return None
    key = str(name).strip()
    if not key:
        return None
    code = _LOCAL_CODE_FALLBACK.get(key)
    if code:
        return code
    hot = StockCodeSearcher().hot_stocks
    for mkt in hot.values():
        if key in mkt:
            return mkt[key]
    return _newsdb_code_for_name(key)


# ---------- 代码 ↔ 名称一致性校验（2026-09-09 北方铜业错码事故后新增） ----------

_NAME_STRIP_SUFFIXES = ('股份有限公司', '有限责任公司', '有限公司', '集团股份', '股份', '集团', '公司')
_NAME_STRIP_PREFIXES = ('*ST', 'ST', 'N', 'C')


def _norm_name(s) -> str:
    """名称归一：去空白（含全角空格）→ 去 *ST/ST/N/C 前缀 → 去股份/集团/公司等后缀
    → 去「-U/-W/-D」等交易所标记后缀（盛科通信-U → 盛科通信）。

    仅用于「是否同一只票」的宽松比较（避免 北方铜业 vs 北方铜业股份 误判为错配）。
    """
    s = str(s or '').strip().replace(' ', '').replace('\u3000', '').replace('\xa0', '')
    if '-' in s:                      # 腾讯对未盈利/同股不同权标的加 -U/-W/-D 后缀
        s = s.split('-')[0]
    changed = True
    while changed:
        changed = False
        for pre in _NAME_STRIP_PREFIXES:
            if s.startswith(pre) and len(s) > len(pre):
                s = s[len(pre):]
                changed = True
    for suf in _NAME_STRIP_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[:-len(suf)]
            break
    return s


def verify_code_name(stock_name, raw_code, allow_network: bool = True):
    """校验「代码 ↔ 名称」是否指向同一只票 → (ok, 该代码的真实名称)。

    ok=False **仅当正向查到名字且与 stock_name 不同**（真错配，如 北方铜业 配 sh605577
    = 龙版传媒）；查不到（本地三级查不到 + 网络失败/未安装）→ ok=True **fail-open**，
    不阻塞批量入池（与 canonical_stock_code 的 fail-closed 语义刻意相反：这里宁漏拦不误拦）。

    名称来源优先级：腾讯实时接口（权威、覆盖全）> 本地三级反查（fallback 反查/hot_stocks/
    newsdb，零网络）。allow_network=False 时只走本地（测试/离线场景）。
    """
    name = str(stock_name or '').strip()
    if not name or not raw_code:
        return True, None
    try:
        canonical = canonical_stock_code(raw_code)
    except ValueError:
        canonical = str(raw_code).strip()
    found = None
    if allow_network:
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            info = StockPriceFetcher().get_realtime_price(canonical)
            found = getattr(info, 'name', None) if info else None
        except Exception:
            found = None
    if not found:
        found = resolve_name_for_code(canonical)
    if not found:
        return True, None
    return (_norm_name(found) == _norm_name(name)), found
