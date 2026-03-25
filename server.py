"""
HTTP 服务模块
FastAPI 应用，提供全文 RSS Feed 端点和状态查询接口
"""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from storage import ArticleStore
from feed_generator import FeedGenerator

logger = logging.getLogger(__name__)


def create_app(
    store: ArticleStore,
    feed_gen: FeedGenerator,
    sources: list[dict],
    feed_items_limit: int = 50,
) -> FastAPI:
    """
    创建 FastAPI 应用
    
    Args:
        store: 数据库存储实例
        feed_gen: Feed 生成器实例
        sources: RSS 源配置列表
        feed_items_limit: 每个 Feed 的文章数量上限
    
    Returns:
        FastAPI 应用实例
    """
    app = FastAPI(
        title="RssCrawler 全文 RSS 代理",
        description="订阅 RSS 源，自动抓取原文全文，对外提供全文 RSS Feed 服务",
        version="1.0.0",
    )

    # 构建源名称到配置的映射
    source_map = {s["name"]: s for s in sources}

    @app.get("/", summary="服务首页")
    async def index():
        """返回服务基本信息和可用端点"""
        return {
            "service": "RssCrawler 全文 RSS 代理",
            "version": "1.0.0",
            "endpoints": {
                "/feeds": "列出所有可用的 Feed 源",
                "/feed/{source_name}": "获取指定源的全文 RSS Feed",
                "/feed/all": "获取所有源的聚合全文 RSS Feed",
                "/status": "查看各源的抓取状态",
            },
        }

    @app.get("/feeds", summary="列出所有 Feed 源")
    async def list_feeds():
        """返回所有已配置的 RSS 源及其 Feed 地址"""
        feeds = []
        for source in sources:
            name = source["name"]
            feeds.append(
                {
                    "name": name,
                    "description": source.get("description", ""),
                    "original_url": source["url"],
                    "feed_url": f"/feed/{name}",
                    "requires_login": source.get("requires_login", False),
                }
            )
        return {"sources": feeds, "aggregate_feed": "/feed/all"}

    @app.get("/feed/all", summary="聚合全文 Feed")
    async def aggregate_feed():
        """返回所有源的聚合全文 RSS Feed"""
        articles = store.get_articles(source_name=None, limit=feed_items_limit)
        xml = feed_gen.generate_feed_xml(articles, source_name=None)
        return Response(content=xml, media_type="application/xml; charset=utf-8")

    @app.get("/feed/{source_name}", summary="指定源的全文 Feed")
    async def source_feed(source_name: str):
        """返回指定源的全文 RSS Feed"""
        if source_name not in source_map:
            raise HTTPException(
                status_code=404,
                detail=f"源 '{source_name}' 不存在。可用源: {list(source_map.keys())}",
            )

        source_config = source_map[source_name]
        articles = store.get_articles(
            source_name=source_name, limit=feed_items_limit
        )
        xml = feed_gen.generate_feed_xml(articles, source_name, source_config)
        return Response(content=xml, media_type="application/xml; charset=utf-8")

    @app.get("/status", summary="抓取状态")
    async def status():
        """返回各源的抓取统计信息"""
        stats = store.get_sources_stats()
        return {
            "sources": stats,
            "configured_sources": [s["name"] for s in sources],
        }

    return app
