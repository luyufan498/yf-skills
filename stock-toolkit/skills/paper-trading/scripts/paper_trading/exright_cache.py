"""除权除息缓存管理器

每日 TTL 缓存，避免重复调用腾讯接口
"""

import json
import fcntl
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from paper_trading.config import get_workspace_config
from paper_trading.exright_fetcher import ExRightFetcher


class ExRightCache:
    """除权除息缓存管理器"""

    def __init__(self, cache_dir: Path = None):
        if cache_dir is None:
            config = get_workspace_config()
            self.cache_dir = config['workspace_root'] / "cache" / "exright"
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fetcher = ExRightFetcher()

    def _get_cache_path(self, stock_code: str) -> Path:
        year = str(datetime.now().year)
        return self.cache_dir / f"{stock_code}_{year}.json"

    def get(self, stock_code: str) -> Optional[Dict]:
        """获取缓存，如果过期或不存在返回 None"""
        cache_path = self._get_cache_path(stock_code)
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cached_at = data.get('cached_at', '')
            today = datetime.now().strftime('%Y-%m-%d')

            # 不是今天缓存的 = 过期
            if not cached_at.startswith(today):
                return None

            return data
        except (json.JSONDecodeError, Exception):
            return None

    def set(self, stock_code: str, events: List[Dict]):
        """写入缓存（带文件锁）"""
        cache_path = self._get_cache_path(stock_code)

        data = {
            "stock_code": stock_code,
            "cached_at": datetime.now().isoformat(),
            "ttl": "daily",
            "source": "tencent_fqline",
            "events": events,
        }

        fd = open(cache_path, 'a')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    def clear(self, stock_code: str = None):
        """清除缓存（某只股票或全部）"""
        if stock_code:
            cache_path = self._get_cache_path(stock_code)
            if cache_path.exists():
                cache_path.unlink()
        else:
            for f in self.cache_dir.glob("*.json"):
                f.unlink()

    def get_events(self, stock_code: str) -> List[Dict]:
        """获取除权事件（带缓存逻辑：命中用缓存，未命中 fetch）"""
        cached = self.get(stock_code)
        if cached is not None:
            return cached.get('events', [])

        # 缓存未命中或过期：fetch 并写入缓存
        events = self._fetcher.fetch_exright_events(stock_code)
        self.set(stock_code, events)
        return events
