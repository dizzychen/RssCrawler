"""
原文抓取模块
携带 Cookie 请求文章详情页，使用 BeautifulSoup 提取正文内容
"""

import os
import time
import random
import logging
import requests
from bs4 import BeautifulSoup
from typing import Optional

logger = logging.getLogger(__name__)

# User-Agent 列表，随机轮换
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


class ContentFetcher:
    """文章原文抓取器"""

    def __init__(self, cookie_file: Optional[str] = None, requires_login: bool = False) -> None:
        """
        Args:
            cookie_file: Cookie 文件路径（纯文本，整行为 Cookie 值）
            requires_login: 是否需要登录才能访问
        """
        self.session = requests.Session()
        self.requires_login = requires_login
        self._setup_cookies(cookie_file)

    def _setup_cookies(self, cookie_file: Optional[str]) -> None:
        """从文件加载 Cookie 到 Session"""
        if not cookie_file:
            if self.requires_login:
                logger.warning("源需要登录但未配置 cookie_file")
            return

        if not os.path.isfile(cookie_file):
            logger.warning("Cookie 文件不存在: %s", cookie_file)
            return

        try:
            with open(cookie_file, "r", encoding="utf-8") as f:
                cookie_str = f.read().strip()

            if not cookie_str:
                logger.warning("Cookie 文件为空: %s", cookie_file)
                return

            # 解析 "key=value; key2=value2" 格式
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    self.session.cookies.set(key.strip(), value.strip())

            logger.info("已加载 Cookie (%d 项) from %s", len(self.session.cookies), cookie_file)
        except Exception as e:
            logger.error("加载 Cookie 文件失败 [%s]: %s", cookie_file, e)

    def fetch_content(
        self,
        url: str,
        content_selector: str,
        max_retries: int = 2,
        delay_range: tuple[float, float] = (1.0, 3.0),
    ) -> Optional[str]:
        """
        抓取文章原文内容
        
        Args:
            url: 文章详情页 URL
            content_selector: 正文 CSS 选择器
            max_retries: 最大重试次数
            delay_range: 请求前随机延迟范围（秒）
        
        Returns:
            正文 HTML 字符串，失败返回 None
        """
        for attempt in range(max_retries + 1):
            try:
                # 随机延迟
                delay = random.uniform(*delay_range)
                time.sleep(delay)

                # 随机 User-Agent
                headers = {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                }

                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=15,
                    allow_redirects=False,
                )

                # 检测 302 重定向（Cookie 失效 / 需要登录）
                if response.status_code in (301, 302, 303, 307, 308):
                    redirect_url = response.headers.get("Location", "")
                    if "login" in redirect_url.lower() or "signin" in redirect_url.lower():
                        logger.warning(
                            "Cookie 可能已失效，被重定向到登录页: %s → %s",
                            url,
                            redirect_url,
                        )
                        return None
                    # 非登录重定向，跟随
                    response = self.session.get(
                        redirect_url,
                        headers=headers,
                        timeout=15,
                    )

                if response.status_code != 200:
                    logger.warning(
                        "请求失败 [%d] %s (第 %d 次)",
                        response.status_code,
                        url,
                        attempt + 1,
                    )
                    if attempt < max_retries:
                        time.sleep(2 * (attempt + 1))  # 递增等待
                    continue

                # 解析 HTML
                response.encoding = response.apparent_encoding or "utf-8"
                soup = BeautifulSoup(response.text, "lxml")

                # 检测页面中是否包含登录表单
                if self.requires_login and _is_login_page(soup):
                    logger.warning("页面似乎是登录页，Cookie 可能已失效: %s", url)
                    return None

                # 提取正文
                content_el = soup.select_one(content_selector)
                if content_el:
                    content_html = str(content_el)
                    logger.debug("成功抓取正文: %s (%d 字符)", url, len(content_html))
                    return content_html
                else:
                    logger.warning("未找到正文元素 [%s]: %s", content_selector, url)
                    return None

            except requests.exceptions.Timeout:
                logger.warning("请求超时: %s (第 %d 次)", url, attempt + 1)
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))
            except requests.exceptions.RequestException as e:
                logger.error("请求异常 [%s]: %s (第 %d 次)", url, e, attempt + 1)
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))
            except Exception as e:
                logger.error("抓取异常 [%s]: %s", url, e)
                return None

        logger.error("抓取失败（已达最大重试次数）: %s", url)
        return None


def _is_login_page(soup: BeautifulSoup) -> bool:
    """检测页面是否为登录页"""
    # 检查常见的登录表单特征
    login_indicators = [
        soup.find("input", {"type": "password"}),
        soup.find("form", {"id": lambda x: x and "login" in x.lower() if x else False}),
        soup.find("form", {"class": lambda x: x and "login" in str(x).lower() if x else False}),
    ]
    return any(login_indicators)
