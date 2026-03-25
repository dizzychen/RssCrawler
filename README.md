# RssCrawler - RSS 全文代理服务

订阅多个 RSS 源，自动抓取文章原文全文内容，存储到本地 SQLite 数据库，并对外提供包含全文的 RSS Feed 服务。

## 功能特点

- 🔄 **定时自动抓取** - 每 10 分钟自动拉取所有 RSS 源更新
- 📄 **全文抓取** - 访问文章详情页提取原文 HTML 内容
- 🗃️ **本地持久存储** - SQLite 数据库，自动去重
- 📡 **全文 RSS Feed** - HTTP 服务 + 静态 XML 文件双模式输出
- 🔧 **多源支持** - YAML 配置文件管理多个 RSS 源
- 🛡️ **防封策略** - 随机延迟、UA 轮换、Cookie 登录支持

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 RSS 源

编辑 `config.yaml`，添加你需要订阅的 RSS 源：

```yaml
sources:
  - name: "cnblogs-news"
    url: "https://feed.cnblogs.com/news/rss"
    cookie_file: "./cookies/cnblogs.txt"
    content_selector: "#news_content"
    requires_login: true
    description: "博客园新闻"
```

### 3. 配置 Cookie（需要登录的源）

1. 浏览器登录目标网站（如 `https://account.cnblogs.com/signin`）
2. 按 F12 打开开发者工具 → Network 标签
3. 访问任意文章页面，在请求头中找到 `Cookie` 字段
4. 复制整个 Cookie 值，保存到对应文件（如 `cookies/cnblogs.txt`）

### 4. 启动服务

```bash
# 启动完整服务（定时抓取 + HTTP 服务）
python main.py

# 指定配置文件和端口
python main.py --config my_config.yaml --port 9090

# 仅执行一次抓取（不启动 HTTP 服务）
python main.py --crawl-only
```

## API 端点

启动后访问 `http://localhost:8080`：

| 端点 | 说明 |
|------|------|
| `GET /` | 服务信息 |
| `GET /feeds` | 列出所有可用的 Feed 源 |
| `GET /feed/{source_name}` | 获取指定源的全文 RSS Feed |
| `GET /feed/all` | 获取所有源的聚合全文 Feed |
| `GET /status` | 查看各源的抓取统计 |
| `GET /docs` | FastAPI 自动生成的 API 文档 |

### 在 RSS 阅读器中订阅

将以下地址添加到你的 RSS 阅读器：

```
# 订阅单个源
http://localhost:8080/feed/cnblogs-news

# 订阅所有源的聚合 Feed
http://localhost:8080/feed/all
```

## 静态 XML 文件

每次抓取完成后会自动在 `output/` 目录生成静态 XML 文件：

```
output/
├── cnblogs-news.xml   # 各源独立 Feed
├── example-blog.xml
└── all.xml            # 聚合 Feed
```

可直接用 Nginx 托管 `output/` 目录，提供静态 Feed 服务。

## 配置说明

`config.yaml` 完整配置项：

```yaml
global:
  update_interval: 10        # 更新间隔（分钟）
  feed_items_limit: 50       # 每个 Feed 最大文章数
  output_dir: "./output"     # 静态 XML 输出目录
  log_level: "INFO"          # 日志级别
  server_port: 8080          # HTTP 服务端口
  server_host: "0.0.0.0"    # 绑定地址
  db_path: "./articles.db"  # 数据库路径

sources:
  - name: "源标识名"          # 唯一标识，用于 URL 路径
    url: "RSS 源地址"         # RSS/Atom Feed URL
    cookie_file: "Cookie路径" # 可选，Cookie 文件路径
    content_selector: "CSS"   # 可选，正文 CSS 选择器
    requires_login: false     # 是否需要登录
    description: "源描述"     # 可选，显示用
```

## 项目结构

```
RssCrawler/
├── main.py              # 主入口
├── config.yaml          # 多源配置
├── rss_parser.py        # RSS 解析模块
├── content_fetcher.py   # 原文抓取模块
├── storage.py           # SQLite 存储模块
├── feed_generator.py    # Feed 生成模块
├── scheduler.py         # 定时调度模块
├── server.py            # HTTP 服务模块
├── templates/
│   └── rss_feed.xml     # RSS XML 模板
├── output/              # 静态 XML 输出
├── logs/                # 日志文件
├── cookies/             # Cookie 文件
└── requirements.txt     # 依赖清单
```

## 服务器部署

项目已部署到 openclaw 服务器，路径 `/opt/RssCrawler`，通过 systemd 管理。

### 常用管理命令

```bash
# 查看服务状态
ssh openclaw "sudo systemctl status rsscrawler"

# 重启服务
ssh openclaw "sudo systemctl restart rsscrawler"

# 停止服务
ssh openclaw "sudo systemctl stop rsscrawler"

# 查看实时日志
ssh openclaw "sudo journalctl -u rsscrawler -f"

# 查看最近 100 行日志
ssh openclaw "sudo journalctl -u rsscrawler -n 100 --no-pager"
```

### 更新部署

```bash
# 同步本地文件到服务器
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude 'articles.db' --exclude 'logs/*.log' --exclude 'output/*.xml' /Users/dizzychen/RssCrawler/ openclaw:/opt/RssCrawler/

# 重启服务使更新生效
ssh openclaw "sudo systemctl restart rsscrawler"
```
