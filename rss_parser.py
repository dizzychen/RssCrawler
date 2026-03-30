"""
RSS 解析模块
使用 feedparser 解析 RSS/Atom 源，提取文章列表
"""

import feedparser
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


def parse_feed(url: str, source_name: str, verify_tls: bool = True) -> list[dict]:
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
        # 先用 requests 下载（支持跳过 SSL 验证等场景），再用 feedparser 解析
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
            },
            verify=verify_tls,
        )
        resp.encoding = resp.apparent_encoding or "utf-8"
        raw_text = resp.text
        feed = feedparser.parse(raw_text)
    except Exception as e:
        logger.error("解析 RSS 源失败 [%s]: %s", source_name, e)
        return []

    # feedparser 成功解析出 entries 则使用
    if feed.entries:
        if feed.bozo:
            logger.debug("RSS 源 [%s] XML 有瑕疵但仍可解析: %s", source_name, feed.bozo_exception)
    else:
        # feedparser 没解析出 entries，尝试 BeautifulSoup 容错解析
        if feed.bozo:
            logger.debug("feedparser 解析失败 [%s]: %s，尝试容错解析", source_name, feed.bozo_exception)
        try:
            articles = _fallback_parse(raw_text, source_name)
            if articles:
                logger.info("RSS 源 [%s] 容错解析完成, 获取 %d 篇文章", source_name, len(articles))
                return articles
        except Exception as e:
            logger.warning("容错解析也失败 [%s]: %s", source_name, e)
        logger.warning("RSS 源 [%s] 未获取到任何文章", source_name)
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


def _fallback_parse(raw_text: str, source_name: str) -> list[dict]:
    """
    使用 BeautifulSoup 容错解析不规范的 RSS/Atom XML。
    当 feedparser 严格解析失败时作为 fallback。
    """
    soup = BeautifulSoup(raw_text, "lxml-xml")
    items = soup.find_all("item") or soup.find_all("entry")

    articles = []
    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")
        title = title_el.get_text(strip=True) if title_el else ""
        # link 可能是文本节点或 href 属性
        link = ""
        if link_el:
            link = link_el.get("href", "") or link_el.get_text(strip=True)

        if not title or not link:
            continue

        # 摘要
        desc_el = item.find("description") or item.find("summary") or item.find("content")
        summary = desc_el.get_text(strip=True) if desc_el else ""

        # 作者
        author_el = item.find("author") or item.find("dc:creator")
        author = author_el.get_text(strip=True) if author_el else ""

        # 时间
        pub_el = item.find("pubDate") or item.find("published") or item.find("updated")
        published_at = ""
        if pub_el:
            try:
                published_at = parsedate_to_datetime(pub_el.get_text(strip=True)).isoformat()
            except (ValueError, TypeError):
                pass

        articles.append({
            "source_name": source_name,
            "title": title,
            "link": link,
            "summary": summary,
            "author": author,
            "published_at": published_at,
        })

    return articles


def get_feed_info(url: str, verify_tls: bool = True) -> dict:
    """
    获取 RSS 源的频道信息
    
    Args:
        url: RSS 源 URL
    
    Returns:
        频道信息字典: title, link, description
    """
    try:
        resp = requests.get(
            url, timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
            },
            verify=verify_tls,
        )
        resp.encoding = resp.apparent_encoding or "utf-8"
        feed = feedparser.parse(resp.text)
        return {
            "title": feed.feed.get("title", ""),
            "link": feed.feed.get("link", ""),
            "description": feed.feed.get("description", ""),
        }
    except Exception as e:
        logger.error("获取 Feed 信息失败: %s", e)
        return {"title": "", "link": "", "description": ""}
