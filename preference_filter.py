"""
偏好筛选模块
基于用户个人偏好，调用 LLM 智能筛选 RSS 文章
支持偏好文件自动压缩、批量 LLM 判断、SQLite 缓存
"""

import glob
import json
import logging
import os
import time
from typing import Optional

from openai import OpenAI

from storage import ArticleStore

logger = logging.getLogger(__name__)


class PreferenceFilter:
    """基于个人偏好的 RSS 文章智能筛选器"""

    def __init__(
        self,
        store: ArticleStore,
        data_dir: str = "./data",
        api_key: str = "",
        api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-plus",
        batch_size: int = 10,
    ) -> None:
        """
        Args:
            store: 数据库存储实例（用于筛选结果缓存）
            data_dir: 偏好数据文件目录
            api_key: DashScope API Key
            api_base: API 基础 URL
            model: 模型名称
            batch_size: 每批判断的文章数量
        """
        self.store = store
        self.data_dir = data_dir
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.batch_size = batch_size

        # 偏好文本缓存
        self._pref_cache: str = ""
        self._pref_mtimes: dict[str, float] = {}  # 文件路径 → mtime

        # OpenAI 兼容客户端
        self._client: Optional[OpenAI] = None
        if api_key:
            self._client = OpenAI(api_key=api_key, base_url=api_base)

    # ── 偏好加载与自动压缩 ──────────────────────────────────

    def load_preferences(self) -> str:
        """
        扫描 data_dir 下所有 memory_export* 文件，自动压缩清洗后返回精简的偏好摘要文本。
        压缩规则：移除 SKIP 行、去重 REC_EXPOSURE、剔除空白段、仅保留灵魂档案核心偏好。
        通过文件 mtime 检测变更，未变更时直接返回内存缓存。
        """
        pattern = os.path.join(self.data_dir, "memory_export*")
        files = sorted(glob.glob(pattern))

        if not files:
            logger.warning("未找到偏好文件: %s", pattern)
            return ""

        # 检查 mtime 是否有变化
        current_mtimes = {}
        for f in files:
            try:
                current_mtimes[f] = os.path.getmtime(f)
            except OSError:
                continue

        if current_mtimes == self._pref_mtimes and self._pref_cache:
            return self._pref_cache

        # 重新解析
        logger.info("偏好文件有更新，重新加载 (%d 个文件)", len(files))
        all_preferences = []
        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw = f.read()
                compressed = self._compress_preference(raw)
                if compressed.strip():
                    all_preferences.append(compressed)
            except Exception as e:
                logger.warning("读取偏好文件失败 [%s]: %s", filepath, e)

        self._pref_cache = "\n".join(all_preferences)
        self._pref_mtimes = current_mtimes
        logger.info("偏好加载完成: 压缩后约 %d 字符", len(self._pref_cache))
        return self._pref_cache

    def _compress_preference(self, raw_text: str) -> str:
        """
        压缩偏好文件原始文本，提取灵魂档案核心偏好段落。
        去除：SKIP 行、重复 REC_EXPOSURE、空白占位段、阅读日志表格。
        """
        lines = raw_text.split("\n")
        result_lines = []
        in_soul_section = False
        in_reading_log = False
        in_reading_profile = False

        for line in lines:
            stripped = line.strip()

            # 检测段落标题
            if stripped.startswith("## 灵魂档案"):
                in_soul_section = True
                in_reading_log = False
                in_reading_profile = False
                continue  # 跳过段落标题本身
            elif stripped.startswith("## 阅读画像"):
                in_soul_section = False
                in_reading_profile = True
                in_reading_log = False
                continue
            elif stripped.startswith("## 阅读日志"):
                in_soul_section = False
                in_reading_profile = False
                in_reading_log = True
                continue

            # 在灵魂档案段内，保留核心内容
            if in_soul_section:
                # 跳过文件头部的元数据行
                if stripped.startswith("# 记忆数据导出") or stripped.startswith("导出时间"):
                    continue
                # 保留非空行
                if stripped:
                    result_lines.append(line)

            # 阅读画像和阅读日志段全部跳过（无价值）

        return "\n".join(result_lines)

    # ── 文章筛选 ──────────────────────────────────────────

    def filter_articles(self, articles: list[dict]) -> list[dict]:
        """
        对文章列表进行偏好筛选，返回符合偏好的文章子集。
        异常安全：整体 try/except 保护，任何异常直接返回原始 articles，绝不影响 Feed 生成。
        """
        if not articles:
            return articles

        if not self._client:
            logger.debug("筛选器未配置 API Key，跳过筛选")
            return articles

        try:
            return self._do_filter(articles)
        except Exception as e:
            logger.warning("偏好筛选异常，降级返回全部文章: %s", e)
            return articles

    def _do_filter(self, articles: list[dict]) -> list[dict]:
        """实际执行筛选逻辑（内部方法）"""
        # 加载偏好
        preferences = self.load_preferences()
        if not preferences:
            logger.info("偏好数据为空，跳过筛选")
            return articles

        # 查缓存：区分已缓存和未缓存的文章
        uncached = []
        cached_results: dict[str, bool] = {}  # link → is_relevant

        for article in articles:
            link = article.get("link", "")
            source_name = article.get("source_name", "")
            cache = self.store.get_filter_cache(link, source_name)
            if cache is not None:
                cached_results[link] = cache
            else:
                uncached.append(article)

        logger.info(
            "筛选: %d 篇文章, %d 篇命中缓存, %d 篇需 LLM 判断",
            len(articles),
            len(cached_results),
            len(uncached),
        )

        # 批量调用 LLM 判断未缓存的文章
        if uncached:
            for i in range(0, len(uncached), self.batch_size):
                batch = uncached[i : i + self.batch_size]
                try:
                    batch_results = self._batch_judge(batch, preferences)
                    # 写入缓存
                    for article in batch:
                        link = article.get("link", "")
                        source_name = article.get("source_name", "")
                        is_relevant = batch_results.get(link, True)  # 默认保留
                        cached_results[link] = is_relevant
                        self.store.set_filter_cache(link, source_name, is_relevant)
                except Exception as e:
                    # 单批次失败：该批次文章全部保留
                    logger.warning("批次 LLM 判断失败，保留该批次 %d 篇文章: %s", len(batch), e)
                    for article in batch:
                        cached_results[article.get("link", "")] = True

        # 根据筛选结果过滤
        filtered = [
            article
            for article in articles
            if cached_results.get(article.get("link", ""), True)
        ]

        logger.info("筛选完成: %d → %d 篇", len(articles), len(filtered))
        return filtered

    def _batch_judge(
        self, articles: list[dict], preferences: str
    ) -> dict[str, bool]:
        """
        调用 LLM 批量判断文章相关性。

        Args:
            articles: 待判断的文章列表
            preferences: 用户偏好文本

        Returns:
            {link: is_relevant} 映射
        """
        # 构造文章列表文本
        article_list = []
        for idx, article in enumerate(articles, 1):
            title = article.get("title", "")
            summary = (article.get("summary") or "")[:200]  # 截断摘要
            article_list.append(f"{idx}. 标题: {title}\n   摘要: {summary}")

        articles_text = "\n".join(article_list)

        prompt = f"""你是一个智能内容筛选助手。根据以下用户偏好，判断每篇文章是否符合用户的兴趣。

## 用户偏好
{preferences}

## 待判断文章
{articles_text}

## 要求
请对每篇文章判断是否符合用户兴趣偏好，返回 JSON 数组格式：
[{{"index": 1, "relevant": true/false}}, ...]

只返回 JSON 数组，不要任何其他文字。"""

        start = time.time()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        logger.info("LLM 批量判断: %d 篇, 耗时 %.1fs", len(articles), elapsed)

        # 解析响应
        content = response.choices[0].message.content or ""
        results = self._parse_llm_response(content, articles)
        return results

    def _parse_llm_response(
        self, content: str, articles: list[dict]
    ) -> dict[str, bool]:
        """解析 LLM 返回的 JSON 结果"""
        results: dict[str, bool] = {}

        try:
            data = json.loads(content)
            # 兼容两种格式：直接数组 or {"results": [...]}
            if isinstance(data, dict):
                data = data.get("results", data.get("articles", []))
            if isinstance(data, list):
                for item in data:
                    idx = item.get("index", 0) - 1  # 转为 0-based
                    if 0 <= idx < len(articles):
                        link = articles[idx].get("link", "")
                        results[link] = bool(item.get("relevant", True))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("LLM 响应解析失败，默认保留全部文章: %s", e)

        # 未覆盖的文章默认保留
        for article in articles:
            link = article.get("link", "")
            if link not in results:
                results[link] = True

        return results
