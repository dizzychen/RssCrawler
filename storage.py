"""
SQLite 存储模块
负责文章的持久化存储、去重和查询操作
"""

import sqlite3
import threading
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ArticleStore:
    """文章存储管理器，封装 SQLite 操作，线程安全"""

    def __init__(self, db_path: str = "articles.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（每次新建，配合线程锁使用）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # 提升并发读写性能
        return conn

    def init_db(self) -> None:
        """初始化数据库表结构"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS articles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_name TEXT NOT NULL,
                        title TEXT NOT NULL,
                        link TEXT NOT NULL,
                        summary TEXT,
                        content TEXT,
                        full_content_fetched BOOLEAN DEFAULT 0,
                        author TEXT,
                        published_at TEXT,
                        created_at TEXT DEFAULT (datetime('now', 'localtime')),
                        UNIQUE(source_name, link)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_source_name 
                    ON articles(source_name)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_created_at 
                    ON articles(created_at DESC)
                """)
                conn.commit()
                logger.info("数据库初始化完成: %s", self.db_path)
            finally:
                conn.close()

    def insert_articles(self, articles: list[dict]) -> int:
        """
        批量插入文章（去重，已存在的自动跳过）
        
        Args:
            articles: 文章字典列表，每个字典需包含:
                - source_name, title, link
                - 可选: summary, author, published_at
        
        Returns:
            新增的文章数量
        """
        if not articles:
            return 0

        with self._lock:
            conn = self._get_conn()
            try:
                inserted = 0
                for article in articles:
                    try:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO articles 
                            (source_name, title, link, summary, author, published_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                article["source_name"],
                                article["title"],
                                article["link"],
                                article.get("summary"),
                                article.get("author"),
                                article.get("published_at"),
                            ),
                        )
                        if conn.total_changes > inserted:
                            inserted = conn.total_changes
                    except sqlite3.Error as e:
                        logger.error("插入文章失败 [%s]: %s", article.get("link"), e)
                conn.commit()
                # 计算实际新增数
                new_count = inserted
                logger.info("批量插入完成: 提交 %d 篇, 新增 %d 篇", len(articles), new_count)
                return new_count
            finally:
                conn.close()

    def update_content(self, link: str, source_name: str, content: str) -> None:
        """
        更新文章的全文内容
        
        Args:
            link: 文章链接
            source_name: 源名称
            content: 全文 HTML 内容
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    UPDATE articles 
                    SET content = ?, full_content_fetched = 1
                    WHERE link = ? AND source_name = ?
                    """,
                    (content, link, source_name),
                )
                conn.commit()
                logger.debug("更新全文: %s", link)
            finally:
                conn.close()

    def mark_fetch_failed(self, link: str, source_name: str) -> None:
        """标记文章全文抓取失败（保留摘要，标记未成功）"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    UPDATE articles 
                    SET full_content_fetched = 0
                    WHERE link = ? AND source_name = ?
                    """,
                    (link, source_name),
                )
                conn.commit()
            finally:
                conn.close()

    def get_articles(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        查询文章列表
        
        Args:
            source_name: 源名称（None 表示所有源）
            limit: 返回数量上限
        
        Returns:
            文章字典列表，按发布时间降序
        """
        with self._lock:
            conn = self._get_conn()
            try:
                if source_name:
                    rows = conn.execute(
                        """
                        SELECT * FROM articles 
                        WHERE source_name = ?
                        ORDER BY published_at DESC, created_at DESC
                        LIMIT ?
                        """,
                        (source_name, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM articles 
                        ORDER BY published_at DESC, created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def get_unfetched(self, source_name: str) -> list[dict]:
        """
        获取指定源中未成功抓取全文的文章
        
        Args:
            source_name: 源名称
        
        Returns:
            未抓取全文的文章字典列表
        """
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM articles 
                    WHERE source_name = ? AND full_content_fetched = 0
                    ORDER BY created_at DESC
                    """,
                    (source_name,),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def get_sources_stats(self) -> list[dict]:
        """获取各源的统计信息"""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT 
                        source_name,
                        COUNT(*) as total,
                        SUM(CASE WHEN full_content_fetched = 1 THEN 1 ELSE 0 END) as fetched,
                        MAX(created_at) as last_update
                    FROM articles
                    GROUP BY source_name
                    """
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def article_exists(self, link: str, source_name: str) -> bool:
        """检查文章是否已存在"""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT 1 FROM articles WHERE link = ? AND source_name = ?",
                    (link, source_name),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
