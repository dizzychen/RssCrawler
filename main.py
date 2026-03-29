"""
RssCrawler - RSS 全文代理服务
主入口：加载配置 → 启动定时抓取 → 启动 HTTP 服务
"""

import os
import sys

# 修复 macOS 上 Python SSL 证书问题
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass
import argparse
import logging
import logging.handlers
import signal
import yaml
import uvicorn

from storage import ArticleStore
from feed_generator import FeedGenerator
from scheduler import CrawlScheduler
from server import create_app
from preference_filter import PreferenceFilter


def load_config(config_path: str) -> dict:
    """加载 YAML 配置文件"""
    if not os.path.isfile(config_path):
        print(f"错误: 配置文件不存在 - {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config or "sources" not in config:
        print("错误: 配置文件格式不正确，缺少 sources 配置")
        sys.exit(1)

    return config


def setup_logging(log_level: str = "INFO") -> None:
    """配置日志系统"""
    os.makedirs("logs", exist_ok=True)

    # 根日志配置
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 日志格式
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件输出（按大小轮转，最多保留 5 个备份，每个 5MB）
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/rsscrawler.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 降低第三方库日志级别
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="RssCrawler - RSS 全文代理服务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                          # 使用默认配置启动
  python main.py --config my_config.yaml  # 指定配置文件
  python main.py --port 9090              # 指定服务端口
  python main.py --crawl-only             # 仅执行一次抓取，不启动服务
        """,
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP 服务端口 (覆盖配置文件)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP 服务绑定地址 (覆盖配置文件)",
    )
    parser.add_argument(
        "--crawl-only",
        action="store_true",
        help="仅执行一次抓取，不启动 HTTP 服务和定时调度",
    )
    args = parser.parse_args()

    # 1. 加载配置
    config = load_config(args.config)
    global_config = config.get("global", {})
    sources = config["sources"]

    # 合并命令行参数
    server_port = args.port or global_config.get("server_port", 8080)
    server_host = args.host or global_config.get("server_host", "0.0.0.0")
    update_interval = global_config.get("update_interval", 10)
    feed_items_limit = global_config.get("feed_items_limit", 50)
    output_dir = global_config.get("output_dir", "./output")
    db_path = global_config.get("db_path", "./articles.db")
    log_level = global_config.get("log_level", "INFO")
    base_url = global_config.get("base_url", f"http://localhost:{server_port}")

    # 2. 初始化日志
    setup_logging(log_level)
    logger = logging.getLogger(__name__)
    logger.info("RssCrawler 启动中...")
    logger.info("配置: %d 个源, 更新间隔 %d 分钟, 端口 %d", len(sources), update_interval, server_port)

    # 3. 初始化核心组件
    store = ArticleStore(db_path=db_path)
    feed_gen = FeedGenerator(
        template_dir="templates",
        output_dir=output_dir,
        base_url=base_url,
        sources=sources,
    )

    # 3.5 初始化偏好筛选器（可选）
    filter_config = config.get("filter", {})
    pref_filter = None
    if filter_config.get("enabled", False):
        # 环境变量优先
        api_key = os.environ.get("DASHSCOPE_API_KEY") or filter_config.get("api_key", "")
        if api_key:
            pref_filter = PreferenceFilter(
                store=store,
                data_dir=filter_config.get("data_dir", "./data"),
                api_key=api_key,
                model=filter_config.get("model", "qwen-plus"),
                batch_size=filter_config.get("batch_size", 10),
            )
            logger.info("偏好筛选器已启用 (模型: %s)", filter_config.get("model", "qwen-plus"))
        else:
            logger.warning("偏好筛选已启用但未配置 API Key，筛选功能不生效")

    # 4. 创建调度器
    crawl_scheduler = CrawlScheduler(
        store=store,
        feed_gen=feed_gen,
        sources=sources,
        update_interval=update_interval,
        feed_items_limit=feed_items_limit,
        pref_filter=pref_filter,
    )

    if args.crawl_only:
        # 仅执行一次抓取
        logger.info("仅执行一次抓取模式")
        crawl_scheduler.crawl_all()
        logger.info("抓取完成，程序退出")
        return

    # 5. 注册信号处理（优雅退出）
    def graceful_shutdown(signum, frame):
        logger.info("收到退出信号，正在停止...")
        crawl_scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # 6. 启动定时调度器（后台线程）
    crawl_scheduler.start()

    # 7. 创建并启动 HTTP 服务（主线程，阻塞）
    app = create_app(
        store=store,
        feed_gen=feed_gen,
        sources=sources,
        feed_items_limit=feed_items_limit,
        pref_filter=pref_filter,
    )

    logger.info("HTTP 服务启动: http://%s:%d", server_host, server_port)
    logger.info("Feed 端点: http://%s:%d/feeds", server_host, server_port)

    uvicorn.run(app, host=server_host, port=server_port, log_level="warning")


if __name__ == "__main__":
    main()
