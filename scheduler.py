"""
定时调度模块
使用 APScheduler 定时执行 RSS 抓取任务
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from rss_parser import parse_feed
from content_fetcher import ContentFetcher
from storage import ArticleStore
from feed_generator import FeedGenerator

logger = logging.getLogger(__name__)


class CrawlScheduler:
    """RSS 抓取定时调度器"""

    def __init__(
        self,
        store: ArticleStore,
        feed_gen: FeedGenerator,
        sources: list[dict],
        update_interval: int = 10,
        feed_items_limit: int = 50,
        pref_filter=None,
    ) -> None:
        """
        Args:
            store: 数据库存储实例
            feed_gen: Feed 生成器实例
            sources: RSS 源配置列表
            update_interval: 更新间隔（分钟）
            feed_items_limit: 每个 Feed 输出的文章数量上限
            pref_filter: 偏好筛选器实例（可选）
        """
        self.store = store
        self.feed_gen = feed_gen
        self.sources = sources
        self.update_interval = update_interval
        self.feed_items_limit = feed_items_limit
        self.pref_filter = pref_filter
        self._fetchers: dict[str, ContentFetcher] = {}

        self.scheduler = BackgroundScheduler()

    def _get_fetcher(self, source: dict) -> ContentFetcher:
        """获取或创建源对应的内容抓取器（复用 Session）"""
        name = source["name"]
        if name not in self._fetchers:
            self._fetchers[name] = ContentFetcher(
                cookie_file=source.get("cookie_file"),
                requires_login=source.get("requires_login", False),
            )
        return self._fetchers[name]

    def crawl_source(self, source: dict) -> None:
        """
        抓取单个 RSS 源
        
        流程：解析 RSS → 入库去重 → 抓取新文章全文 → 导出静态 XML
        """
        name = source["name"]
        url = source["url"]
        selector = source.get("content_selector", "")

        logger.info("=== 开始抓取源: %s ===", name)

        # 1. 解析 RSS
        articles = parse_feed(url, name)
        if not articles:
            logger.warning("源 [%s] 未获取到任何文章", name)
            return

        # 2. 入库（自动去重）
        new_count = self.store.insert_articles(articles)
        logger.info("源 [%s]: 解析 %d 篇, 新增 %d 篇", name, len(articles), new_count)

        # 3. 抓取未获取全文的文章
        if selector:
            unfetched = self.store.get_unfetched(name)
            if unfetched:
                fetcher = self._get_fetcher(source)
                logger.info("源 [%s]: 开始抓取 %d 篇文章的全文", name, len(unfetched))

                for i, article in enumerate(unfetched, 1):
                    link = article["link"]

                    # 跳过付费/会员文章（如少数派 /prime/），未登录无法抓取正文
                    if "/prime/" in link:
                        logger.debug("  [%d/%d] 跳过付费文章: %s", i, len(unfetched), link)
                        self.store.mark_fetch_failed(link, name)
                        continue

                    logger.info("  [%d/%d] 抓取: %s", i, len(unfetched), link)

                    content = fetcher.fetch_content(link, selector)
                    if content:
                        self.store.update_content(link, name, content)
                    else:
                        self.store.mark_fetch_failed(link, name)
            else:
                logger.info("源 [%s]: 所有文章已抓取全文", name)
        else:
            logger.info("源 [%s]: 未配置 content_selector，跳过全文抓取", name)

        # 4. 导出静态 XML
        stored_articles = self.store.get_articles(
            source_name=name, limit=self.feed_items_limit
        )
        if stored_articles:
            # 偏好筛选（如果启用）
            if self.pref_filter:
                stored_articles = self.pref_filter.filter_articles(stored_articles)
            self.feed_gen.export_static_xml(stored_articles, name, source)

        logger.info("=== 源 [%s] 抓取完成 ===", name)

    def crawl_all(self) -> None:
        """抓取所有配置的 RSS 源"""
        logger.info("====== 开始全量抓取 (%d 个源) ======", len(self.sources))

        for source in self.sources:
            try:
                self.crawl_source(source)
            except Exception as e:
                logger.error("抓取源 [%s] 异常: %s", source["name"], e, exc_info=True)

        # 生成聚合 Feed
        self.feed_gen.export_all_static(
            self.store, self.sources, self.feed_items_limit,
            pref_filter=self.pref_filter,
        )

        logger.info("====== 全量抓取完成 ======")

    def start(self) -> None:
        """启动定时调度器"""
        # 添加定时任务
        self.scheduler.add_job(
            self.crawl_all,
            trigger=IntervalTrigger(minutes=self.update_interval),
            id="crawl_all",
            name="RSS 全文抓取",
            max_instances=1,  # 防止任务重叠
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(
            "定时调度器已启动: 每 %d 分钟执行一次", self.update_interval
        )

        # 首次立即执行一次
        logger.info("首次启动，立即执行一次抓取...")
        self.crawl_all()

    def stop(self) -> None:
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("定时调度器已停止")
