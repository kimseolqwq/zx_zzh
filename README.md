# 择机：基于多本地大模型融合的手机购买推荐系统

本项目用于“专业技能实训”，将品牌官网参数、官方旗舰店价格、SQLite 数据库和三个本地大模型组合成一个可解释的手机购买推荐系统。

## 已实现功能

- 手机品牌、型号、内存版本、平台商品和价格快照的关系型数据模型；
- 管理员登录、CSRF 防护、Argon2 密码哈希、登录限流和操作日志；
- 手机、版本、平台链接和价格的增删改查；
- Qwen3 1.7B、Qwen2.5 1.5B、Gemma3 1B 并行调用，7 秒超时自动降级；
- JSON 输出校验和失败降级；
- 五维融合算法：需求匹配 35%、模型共识 25%、数据可信度 20%、性价比 15%、时效性 5%；
- 预算、品牌、最低存储作为数据库硬约束；无符合项时明确提示调整条件，不强行推荐超预算机型；
- 推荐原因、模型投票、置信度、参数和价格来源展示；
- Top 1 展示五项原始分、权重、实际贡献分以及可追溯的结论证据链；
- 公开“系统评估”页实时汇总数据覆盖率、价格新鲜度、P50/P95 延迟、模型成功率、JSON 成功率与输出速度；
- 手机库支持按品牌分组、品牌数量统计、关键词搜索和三种排序；
- 推荐卡片展示官网真实产品图，以及京东、天猫、拼多多各自最新价格；
- Top 1 默认展开，鼠标悬停或键盘聚焦其他推荐时动态切换展开卡片；
- 普通价、国补价、百亿补贴价均可点击：有已核验商品链接时直接进入商品页，否则进入对应平台搜索页并明确标注；
- 价格卡片展示最近核验时间、7 天过期提醒、促销标签、库存和审核证据状态；
- “商品页实显国补”与“规则估算国补”分开展示，避免把估算金额误报成真实成交价；
- 官方规格页保守解析器和低频安全抓取器；
- 电商公开页面留证采集框架，不绕过登录和验证码；
- 模型响应时间、输出速度、JSON 成功率和爬虫日志记录；
- 自动化测试。
- 30 条固定查询基准、四组对照/消融实验、逐模型 P50/P95 与人工评分模板；
- 官网历史证据离线重解析与数据缺口队列，便于断点续做且不重复请求网站。

## 快速运行

1. 确保 Ollama 已启动。在线推荐默认使用可同时放入 8GB 显存的 `qwen3:1.7b`、`qwen2.5:1.5b`、`gemma3:1b`；原来的 8B/7B/4B 模型可保留用于离线基准。

```powershell
ollama pull qwen3:1.7b
ollama pull qwen2.5:1.5b
ollama pull gemma3:1b
```
2. 首次安装运行：`powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`
3. 启动系统：`powershell -ExecutionPolicy Bypass -File .\run.ps1`
4. 浏览器访问：`http://127.0.0.1:8000`

默认管理员账号仅用于本地首次演示：

- 用户名：`admin`
- 密码：`Admin@123456`

正式展示前请在 `.env` 中修改密码并删除 `data/phone_recommender.db`，让程序按新密码重新创建管理员。

## 批量数据维护

项目已附带 10 个品牌、72 个官方来源页的可复现采集清单。执行官网采集：

```powershell
.\.venv\Scripts\python.exe .\scripts\collect_catalog.py
.\.venv\Scripts\python.exe .\scripts\reparse_official_evidence.py
.\.venv\Scripts\python.exe .\scripts\audit_data_quality.py
.\.venv\Scripts\python.exe .\scripts\generate_gap_queue.py
```

离线重解析会合并全部历史采集报告，并为每款手机选择最新一次成功保存的官网证据；定向采集产生的小报告不会再遮蔽较早的完整采集结果。整个过程网络请求数为 0。

程序遵守 `robots.txt`；禁止自动抓取的站点使用 `config/manual_verified_supplement.csv` 经人工核验导入，不绕过限制。

先复制 `config/catalog_template.csv`，每行填写一个“手机版本 + 平台商品”。同一手机版本可写三行，分别对应京东、天猫、拼多多。建议先校验再导入：

```powershell
.\.venv\Scripts\python.exe .\scripts\import_catalog.py .\config\my_catalog.csv --dry-run
.\.venv\Scripts\python.exe .\scripts\import_catalog.py .\config\my_catalog.csv
```

生成三个电商平台的逐 SKU 审核队列，使用已有浏览器登录态留证后再导入：

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_price_queue.py
.\.venv\Scripts\python.exe .\scripts\capture_price.py "商品链接" "任务编号"
.\.venv\Scripts\python.exe .\scripts\import_reviewed_prices.py .\data\review\price_review_queue.csv --dry-run
.\.venv\Scripts\python.exe .\scripts\import_reviewed_prices.py .\data\review\price_review_queue.csv
```

队列按“缺少平台数据优先、发布时间倒序”排列；再次生成会按品牌、型号、内存和平台合并旧队列，保留已经填写的商品链接与审核进度。

审核导入器会校验平台域名、官方店名称、审核人、价格范围以及 `data/raw` 下真实存在的文本/截图证据；重复导入同一批价格不会产生重复快照。`pending` 行不会写入数据库。

使用专用 Edge 会话登录三平台后，可对已经填写 `product_url` 的队列行分批留证并回填候选价格：

```powershell
.\.venv\Scripts\python.exe .\scripts\setup_market_browser.py
.\.venv\Scripts\python.exe .\scripts\discover_market_candidates.py .\data\review\price_review_queue.csv --platform jd --limit 10
.\.venv\Scripts\python.exe .\scripts\capture_price_queue.py .\data\review\price_review_queue.csv --platform jd --limit 10
```

商品发现只输出最多 5 个带可解释匹配分数的候选链接；需要先核对店铺与内存版本，再把正确链接复制到价格队列。采集结果只会标为 `needs_review`。核对商品版本、官方店、页面价格和截图后，填写审核人并把该行改为 `approved`，再运行上面的审核导入命令。浏览器会话、页面正文和截图均只保存在本机且被 Git 忽略。

导出四张核心表并生成 SQLite 备份：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_data.py
```

## 数据原则

- 手机参数优先来自品牌官网；
- 商品链接必须经过官方旗舰店白名单确认；
- 未核验到商品直达链接时，界面只提供带手机型号关键词的平台搜索入口，不将搜索页标为官方商品页；
- 产品图片只接受品牌官网或官方静态资源地址，并保存图片来源页面；
- 普通售价、国补价、百亿补贴价分别保存；
- 资格价格只作条件展示，最终以用户结算页为准；
- 采集失败保存原因，不用 0 或猜测值代替；
- 所有自动价格都保留原始文本或截图证据。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

运行固定基准与消融实验（模型响应会逐题缓存）：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_benchmark.py
```

验证在线推荐的 10 秒延迟目标：

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_fast_online.py --limit-seconds 10
```

结果位于 `outputs/benchmark/`，包括原始 JSON、逐题 CSV、实验报告、图表和人工评分模板。

## 项目结构

- `app/models.py`：SQLite 表结构；
- `app/routers/`：公开页面与管理员 CRUD；
- `app/services/`：Ollama 调用和融合推荐；
- `app/crawlers/`：官网与电商采集；
- `app/templates/`、`app/static/`：Web UI；
- `tests/`：自动化测试；
- `docs/`：架构、数据库、采集和性能评估说明；
- `data/`：本地数据库与采集证据，不提交版本库。
