"""市场新闻数据获取器

支持多个新闻源：财联社、新浪财经、TradingView
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import requests
import time


# API 配置常量
class APIConfig:
    """API 配置常量"""

    # 财联社
    CLS_URL = "https://www.cls.cn/nodeapi/telegraphList"
    CLS_REFERER = "https://www.cls.cn/"

    # 新浪财经
    SINA_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
    SINA_REFERER = "https://finance.sina.com.cn"
    SINA_ZHIBO_ID = 152
    SINA_FEED_ID = 4161089

    # TradingView
    TRADINGVIEW_URL = "https://news-mediator.tradingview.com/news-flow/v2/news"
    TRADINGVIEW_REFERER = "https://cn.tradingview.com/"
    TRADINGVIEW_FILTER = "lang:zh-Hans"

    # 通用配置
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    )
    DEFAULT_TIMEOUT = 10
    REQUEST_TIMEOUT_MULTIPLIER = 1.5  # TradingView 需要更长的超时
    RATE_LIMIT_DELAY = 0.5  # 秒，API 调用之间的延迟
    MAX_RETRY_ATTEMPTS = 2
    RETRY_DELAY = 1  # 秒

    # 限制范围
    MIN_LIMIT = 1
    MAX_LIMIT = 100


class RateLimiter:
    """简单的速率限制器"""

    def __init__(self, delay: float = 0.5):
        """
        初始化速率限制器

        Args:
            delay: API 调用之间的延迟（秒）
        """
        self.delay = delay
        self.last_call_time: Optional[float] = None

    def wait(self) -> None:
        """等待直到可以进行下一次 API 调用"""
        if self.last_call_time is not None:
            elapsed = time.time() - self.last_call_time
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self.last_call_time = time.time()


class MarketNewsFetcher:
    """市场新闻数据获取器"""

    def __init__(self, timeout: int = APIConfig.DEFAULT_TIMEOUT, enable_rate_limit: bool = True):
        """
        初始化新闻获取器

        Args:
            timeout: 请求超时时间（秒）
            enable_rate_limit: 是否启用速率限制
        """
        self.timeout = timeout
        self.enable_rate_limit = enable_rate_limit
        self.rate_limiter = RateLimiter(APIConfig.RATE_LIMIT_DELAY) if enable_rate_limit else None
        self.headers = {
            "User-Agent": APIConfig.USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    @staticmethod
    def _validate_limit(limit: int) -> None:
        """
        验证 limit 参数

        Args:
            limit: 要验证的限制值

        Raises:
            ValueError: 如果 limit 超出有效范围
        """
        if not isinstance(limit, int):
            raise ValueError(f"limit 必须是整数，当前类型: {type(limit).__name__}")
        if limit < APIConfig.MIN_LIMIT or limit > APIConfig.MAX_LIMIT:
            raise ValueError(f"limit 必须在 {APIConfig.MIN_LIMIT} 到 {APIConfig.MAX_LIMIT} 之间，当前值: {limit}")

    def _fetch_with_retry(
        self, url: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None, timeout_multiplier: float = 1.0
    ) -> requests.Response:
        """
        带重试机制的 HTTP 请求

        Args:
            url: 请求 URL
            headers: 请求头
            params: 查询参数
            timeout_multiplier: 超时时间倍数

        Returns:
            HTTP 响应对象

        Raises:
            requests.RequestException: 如果所有重试都失败
        """
        last_exception = None
        actual_timeout = self.timeout * timeout_multiplier

        for attempt in range(APIConfig.MAX_RETRY_ATTEMPTS):
            try:
                # 速率限制
                if self.enable_rate_limit and self.rate_limiter:
                    self.rate_limiter.wait()

                response = requests.get(url, headers=headers, params=params, timeout=actual_timeout)
                response.raise_for_status()
                return response

            except requests.exceptions.Timeout as e:
                last_exception = e
                print(f"超时 (尝试 {attempt + 1}/{APIConfig.MAX_RETRY_ATTEMPTS}): {e}")
            except requests.exceptions.HTTPError as e:
                last_exception = e
                print(f"HTTP 错误 (尝试 {attempt + 1}/{APIConfig.MAX_RETRY_ATTEMPTS}): {e}")
                # 对于 4xx 错误，不重试
                if 400 <= e.response.status_code < 500:
                    raise
            except requests.exceptions.RequestException as e:
                last_exception = e
                print(f"请求错误 (尝试 {attempt + 1}/{APIConfig.MAX_RETRY_ATTEMPTS}): {e}")

            # 重试前等待
            if attempt < APIConfig.MAX_RETRY_ATTEMPTS - 1:
                time.sleep(APIConfig.RETRY_DELAY * (attempt + 1))

        raise last_exception or requests.RequestException("请求失败")

    def _safe_timestamp_to_datetime(self, timestamp: float) -> datetime:
        """
        安全地将时间戳转换为 datetime（时区感知）

        Args:
            timestamp: Unix 时间戳

        Returns:
            时区感知的 datetime 对象
        """
        try:
            # 验证时间戳范围（1970-01-01 到 2100-01-01）
            if timestamp < 0 or timestamp > 4102444800:  # 2100-01-01 的秒数
                raise ValueError(f"无效的时间戳: {timestamp}")

            return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
        except (ValueError, OSError) as e:
            print(f"时间戳转换失败: {e}，使用当前时间")
            return datetime.now(tz=timezone.utc).astimezone()

    def _safe_parse_datetime(self, datetime_str: str, datetime_format: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
        """
        安全地解析 datetime 字符串

        Args:
            datetime_str: datetime 字符串
            datetime_format: 期望的格式

        Returns:
            解析后的 datetime 对象，失败时返回 None
        """
        if not datetime_str:
            return None

        try:
            return datetime.strptime(datetime_str, datetime_format)
        except ValueError as e:
            print(f"datetime 解析失败: {e}")
            return None

    def fetch_cls_news(self, limit: int = 30) -> List[Dict[str, Any]]:
        """
        获取财联社电报新闻

        Args:
            limit: 获取新闻数量

        Returns:
            新闻列表

        Raises:
            ValueError: 如果 limit 参数无效
        """
        self._validate_limit(limit)

        headers = {"Referer": APIConfig.CLS_REFERER, **self.headers}

        try:
            response = self._fetch_with_retry(APIConfig.CLS_URL, headers)
            data = response.json()

            if data.get("error") == 0:
                roll_data = data.get("data", {}).get("roll_data", [])
                news_list: List[Dict[str, Any]] = []

                for item in roll_data[:limit]:
                    # 验证项目是字典
                    if not isinstance(item, dict):
                        continue

                    ctime = item.get("ctime", 0)
                    data_time = self._safe_timestamp_to_datetime(ctime)

                    news: Dict[str, Any] = {
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "time": data_time.strftime("%H:%M:%S"),
                        "date": data_time.strftime("%Y-%m-%d"),
                        "datetime": data_time.isoformat(),
                        "url": item.get("shareurl", ""),
                        "source": "财联社电报",
                        "is_red": item.get("level", "C") != "C",
                        "tags": [],
                    }

                    # 提取标签
                    subjects = item.get("subjects", [])
                    if isinstance(subjects, list):
                        news["tags"] = [s.get("subject_name", "") for s in subjects if isinstance(s, dict)]

                    news_list.append(news)

                return news_list
            else:
                error_msg = data.get("message", "未知错误")
                print(f"财联社 API 返回错误: {error_msg}")

        except requests.exceptions.JSONDecodeError as e:
            print(f"财联社 API 响应格式错误: {e}")
        except (requests.RequestException, ValueError) as e:
            print(f"获取财联社新闻时出错: {e}")

        return []

    def fetch_sina_live_news(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取新浪财经直播新闻

        Args:
            limit: 获取新闻数量

        Returns:
            新闻列表

        Raises:
            ValueError: 如果 limit 参数无效
        """
        self._validate_limit(limit)

        headers = {"Referer": APIConfig.SINA_REFERER, **self.headers}

        params = {
            "page": 1,
            "page_size": limit,
            "zhibo_id": APIConfig.SINA_ZHIBO_ID,
            "tag_id": 0,
            "dire": "f",
            "dpc": 1,
            "pagesize": limit,
            "id": APIConfig.SINA_FEED_ID,
            "type": 0,
            "_": int(time.time()),
        }

        try:
            response = self._fetch_with_retry(APIConfig.SINA_URL, headers, params)
            data = response.json()

            # 新浪API现在的数据结构: result.data.feed.dict{'list': [...]}
            result = data.get("result", {}).get("data", {})
            feed_obj = result.get("feed", {})

            # 兼容新旧格式：feed 可能是 dict 或 list
            if isinstance(feed_obj, dict):
                feed_list = feed_obj.get("list", [])
            else:
                feed_list = feed_obj if isinstance(feed_obj, list) else []

            news_list: List[Dict[str, Any]] = []

            for item in feed_list:
                # 验证项目是字典
                if not isinstance(item, dict):
                    continue

                rich_text = item.get("rich_text", "")
                title_match = rich_text.split("】")

                create_time = item.get("create_time", "")
                parsed_datetime = self._safe_parse_datetime(create_time)

                if parsed_datetime:
                    date = parsed_datetime.strftime("%Y-%m-%d")
                    time_str = parsed_datetime.strftime("%H:%M:%S")
                    iso_datetime = parsed_datetime.isoformat()
                else:
                    date, time_str, iso_datetime = "", "", ""

                news: Dict[str, Any] = {
                    "content": rich_text,
                    "title": title_match[0].replace("【", "") + "】" if len(title_match) > 1 else "",
                    "time": time_str,
                    "date": date,
                    "datetime": iso_datetime,
                    "source": "新浪财经",
                    "tags": [],
                }

                # 提取标签
                tags = item.get("tag", [])
                if isinstance(tags, list):
                    news["tags"] = [t.get("name", "") if isinstance(t, dict) else str(t) for t in tags]

                # 判断是否重要（包含"焦点"标签）
                news["is_red"] = "焦点" in news["tags"]

                news_list.append(news)

            return news_list

        except requests.exceptions.JSONDecodeError as e:
            print(f"新浪 API 响应格式错误: {e}")
        except (requests.RequestException, ValueError) as e:
            print(f"获取新浪新闻时出错: {e}")

        return []

    def fetch_tradingview_news(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取TradingView中文新闻

        Args:
            limit: 获取新闻数量

        Returns:
            新闻列表

        Raises:
            ValueError: 如果 limit 参数无效
        """
        self._validate_limit(limit)

        headers = {
            "Host": "news-mediator.tradingview.com",
            "Origin": APIConfig.TRADINGVIEW_REFERER,
            "Referer": APIConfig.TRADINGVIEW_REFERER,
            **self.headers,
        }

        params = {"filter": APIConfig.TRADINGVIEW_FILTER, "client": "screener", "streaming": "false"}

        try:
            response = self._fetch_with_retry(
                APIConfig.TRADINGVIEW_URL, headers, params, timeout_multiplier=APIConfig.REQUEST_TIMEOUT_MULTIPLIER
            )
            data = response.json()

            items = data.get("items", [])[:limit]
            news_list: List[Dict[str, Any]] = []

            for item in items:
                # 验证项目是字典
                if not isinstance(item, dict):
                    continue

                published = item.get("published", 0)
                data_time = self._safe_timestamp_to_datetime(published)

                news_id = item.get("id", "")

                # TradingView API 格式：title 直接在根层级
                title = item.get("title", "")

                news: Dict[str, Any] = {
                    "id": news_id,
                    "title": title,
                    "content": title,  # TradingView 通常只有标题，用 title 作为 content
                    "description": "",
                    "time": data_time.strftime("%H:%M:%S"),
                    "date": data_time.strftime("%Y-%m-%d"),
                    "datetime": data_time.isoformat(),
                    "url": f"https://cn.tradingview.com/news/{news_id}",
                    "source": "TradingView外媒",
                    "is_red": False,
                    "tags": item.get("tags", []),
                }

                news_list.append(news)

            return news_list

        except requests.exceptions.JSONDecodeError as e:
            print(f"TradingView API 响应格式错误: {e}")
        except (requests.RequestException, ValueError) as e:
            print(f"获取 TradingView 新闻时出错: {e}")

        return []

    def fetch_all_news(self, limit_per_source: int = 20) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取所有源的新闻

        Args:
            limit_per_source: 每个源获取的新闻数量

        Returns:
            包含所有源新闻的字典

        Raises:
            ValueError: 如果 limit_per_source 参数无效
        """
        return {
            "cls_news": self.fetch_cls_news(limit_per_source),
            "sina_live": self.fetch_sina_live_news(limit_per_source),
            "tradingview": self.fetch_tradingview_news(limit_per_source // 2),
        }

    def get_latest_news(self, total_limit: int = 30) -> List[Dict[str, Any]]:
        """
        获取最新新闻（合并所有源）

        Args:
            total_limit: 总新闻数量

        Returns:
            按时间排序的新闻列表

        Raises:
            ValueError: 如果 total_limit 参数无效
        """
        self._validate_limit(total_limit)
        all_sources = self.fetch_all_news(limit_per_source=total_limit)

        # 合并所有新闻，创建新对象避免修改原始数据
        all_news: List[Dict[str, Any]] = []
        for source, news_list in all_sources.items():
            for news_item in news_list:
                # 创建新字典，避免修改原始新闻对象
                news_copy: Dict[str, Any] = {**news_item, "source_type": source}
                all_news.append(news_copy)

        # 按时间排序（处理无效 datetime）
        def get_datetime(item: Dict[str, Any]) -> str:
            return item.get("datetime", "")

        all_news.sort(key=get_datetime, reverse=True)

        return all_news[:total_limit]
