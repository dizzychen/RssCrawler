"""
RSS 解析模块
使用 feedparser 解析 RSS/Atom 源，提取文章列表
"""

import feedparser
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


def parse_feed(url: str, source_name: str) -> list[dict]:
    """
    解析 RSS/Atom 源，返回标准化的文章列表
    
    Args:
        url: RSS 源 URL
        source_name: 源标识名称
    
    Returns:
        文章字典列表，每个字典包含:
        - source_name: 源名称
        - title: 文章标题
        - link: 原文链接
        - summary: 摘要内容
        - author: 作者
        - published_at: 发布时间 (ISO 格式字符串)
    """
    logger.info("开始解析 RSS 源: %s (%s)", source_name, url)

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("解析 RSS 源失败 [%s]: %s", source_name, e)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("RSS 源解析异常 [%s]: %s", source_name, feed.bozo_exception)
        return []

    articles = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()

        if not title or not link:
            logger.debug("跳过无标题或无链接的条目")
            continue

        # 提取摘要
        summary = ""
        if entry.get("summary"):
            summary = entry.summary.strip()
        elif entry.get("description"):
            summary = entry.description.strip()

        # 提取作者
        author = entry.get("author", "").strip()

        # 解析发布时间
        published_at = _parse_publish_time(entry)

        articles.append(
            {
                "source_name": source_name,
                "title": title,
                "link": link,
                "summary": summary,
                "author": author,
                "published_at": published_at,
            }
        )

    logger.info("RSS 源 [%s] 解析完成, 获取 %d 篇文章", source_name, len(articles))
    return articles


def _parse_publish_time(entry) -> str:
    """
    解析文章发布时间，尝试多种格式
    
    Returns:
        ISO 格式时间字符串，解析失败返回空字符串
    """
    # 优先使用 feedparser 已解析的时间结构
    if entry.get("published_parsed"):
        try:
            dt = datetime(*entry.published_parsed[:6])
            return dt.isoformat()
        except (ValueError, TypeError):
            pass

    if entry.get("updated_parsed"):
        try:
            dt = datetime(*entry.updated_parsed[:6])
            return dt.isoformat()
        except (ValueError, TypeError):
            pass

    # 尝试直接解析字符串
    for field in ("published", "updated"):
        raw = entry.get(field, "")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.isoformat()
            except (ValueError, TypeError):
                pass

    return ""


def get_feed_info(url: str) -> dict:
    """
    获取 RSS 源的频道信息
    
    Args:
        url: RSS 源 URL
    
    Returns:
        频道信息字典: title, link, description
    """
    try:
        feed = feedparser.parse(url)
        return {
            "title": feed.feed.get("title", ""),
            "link": feed.feed.get("link", ""),
            "description": feed.feed.get("description", ""),
        }
    except Exception as e:
        logger.error("获取 Feed 信息失败: %s", e)
        return {"title": "", "link": "", "description": ""}
