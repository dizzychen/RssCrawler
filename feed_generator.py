"""
Feed 生成模块
从数据库读取文章，生成标准 RSS 2.0 XML 输出
支持动态字符串生成（HTTP 响应）和静态 XML 文件导出
"""

import os
import logging
from datetime import datetime
from email.utils import formatdate
from time import mktime

from typing import Optional

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


class FeedGenerator:
    """RSS Feed 生成器"""

    def __init__(
        self,
        template_dir: str = "templates",
        output_dir: str = "output",
        server_host: str = "localhost",
        server_port: int = 8080,
    ) -> None:
        self.output_dir = output_dir
        self.base_url = f"http://{server_host}:{server_port}"

        # 初始化 Jinja2 模板引擎
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=False,  # RSS XML 需要手动控制转义
        )
        self.template = self.env.get_template("rss_feed.xml")

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)

    def generate_feed_xml(
        self,
        articles: list[dict[str, object]],
        source_name: Optional[str] = None,
        source_config: Optional[dict[str, object]] = None,
    ) -> str:
        """
        生成 RSS 2.0 XML 字符串
        
        Args:
            articles: 文章字典列表
            source_name: 源名称（None 表示聚合 Feed）
            source_config: 源配置（包含 url, description 等）
        
        Returns:
            RSS XML 字符串
        """
        if source_name:
            channel_title = f"{source_name} - 全文 Feed"
            channel_link = source_config.get("url", "") if source_config else ""
            channel_description = (
                source_config.get("description", f"{source_name} 的全文 RSS Feed")
                if source_config
                else f"{source_name} 的全文 RSS Feed"
            )
            self_link = f"{self.base_url}/feed/{source_name}"
        else:
            channel_title = "Rss聚合Feed"
            channel_link = self.base_url
            channel_description = "所有订阅源的聚合全文 RSS Feed"
            self_link = f"{self.base_url}/feed/all"

        # 格式化文章的发布时间为 RFC 2822 格式
        feed_items = []
        for article in articles:
            item = dict(article)
            item["source_url"] = (
                source_config.get("url", "") if source_config else ""
            )
            if not item.get("source_name"):
                item["source_name"] = source_name or "unknown"

            # 转换时间格式
            if item.get("published_at"):
                item["published_at"] = _to_rfc2822(str(item["published_at"]))

            feed_items.append(item)

        now_rfc2822 = formatdate(localtime=True)

        xml = self.template.render(
            channel_title=channel_title,
            channel_link=channel_link,
            channel_description=channel_description,
            last_build_date=now_rfc2822,
            self_link=self_link,
            items=feed_items,
        )

        return xml

    def export_static_xml(
        self,
        articles: list[dict[str, object]],
        source_name: str,
        source_config: Optional[dict[str, object]] = None,
    ) -> str:
        """
        生成并写入静态 XML 文件
        
        Args:
            articles: 文章字典列表
            source_name: 源名称
            source_config: 源配置
        
        Returns:
            输出文件路径
        """
        xml = self.generate_feed_xml(articles, source_name, source_config)
        filename = f"{source_name}.xml"
        filepath = os.path.join(self.output_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(xml)
            logger.info("静态 XML 已导出: %s", filepath)
        except Exception as e:
            logger.error("导出静态 XML 失败 [%s]: %s", filepath, e)

        return filepath

    def export_all_static(
        self,
        store,
        sources: list[dict[str, object]],
        feed_items_limit: int = 50,
    ) -> None:
        """
        为所有源生成静态 XML 文件（含聚合 Feed）
        
        Args:
            store: ArticleStore 实例
            sources: 源配置列表
            feed_items_limit: 每个 Feed 的文章数量上限
        """
        for source in sources:
            name = str(source["name"])
            articles = store.get_articles(source_name=name, limit=feed_items_limit)
            if articles:
                self.export_static_xml(articles, name, source)
            else:
                logger.debug("源 [%s] 无文章，跳过静态导出", name)

        # 聚合 Feed
        all_articles = store.get_articles(source_name=None, limit=feed_items_limit)
        if all_articles:
            xml = self.generate_feed_xml(all_articles, source_name=None)
            filepath = os.path.join(self.output_dir, "all.xml")
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(xml)
                logger.info("聚合静态 XML 已导出: %s", filepath)
            except Exception as e:
                logger.error("导出聚合 XML 失败: %s", e)


def _to_rfc2822(time_str: str) -> str:
    """将 ISO 格式时间字符串转换为 RFC 2822 格式"""
    if not time_str:
        return ""
    try:
        dt = datetime.fromisoformat(time_str)
        return formatdate(mktime(dt.timetuple()), localtime=True)
    except (ValueError, TypeError):
        return time_str  # 无法转换则原样返回
