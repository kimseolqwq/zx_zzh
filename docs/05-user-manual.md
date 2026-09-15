# 使用手册

## 启动

确认 Ollama 正在运行且已下载三个模型，然后在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

访问 `http://127.0.0.1:8000`。首页输入预算和用途，系统先通过硬约束和领域评分精筛 5 款不同机型，再并行调用三个轻量模型复核，并返回 3 个带自然语言解释的结果。单次模型等待上限为 8.5 秒，模型在一次调用后驻留 30 分钟；个别模型超时不会阻止规则层返回合法结果。点击推荐卡片可查看 CPU、相机、屏幕、电池、内存版本和平台价格。

## 管理数据

访问 `/admin/login`，首次演示账号为 `admin` / `Admin@123456`。请在正式演示前修改 `.env` 的管理员密码并重新建立数据库。

后台可新增品牌、手机、内存版本、平台商品链接和价格快照，编辑型号状态，停用错误记录，并查看模型性能与操作日志。停用不会删除历史证据。

## 批量导入

复制 `config/catalog_template.csv`。一行代表一个内存版本在一个平台上的商品；同一版本若覆盖三平台，填写三行。先运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\import_catalog.py .\config\my_catalog.csv --dry-run
```

通过后去掉 `--dry-run` 正式入库。Excel 保存时建议选择“CSV UTF-8”。

## 备份与测试

```powershell
.\.venv\Scripts\python.exe .\scripts\export_data.py
.\.venv\Scripts\python.exe -m pytest -q
```

导出目录含四张 CSV 和 SQLite 备份。每次大批量导入或改爬虫规则前都应先备份。

## 常见问题

- 页面显示模型离线：先打开 Ollama，执行 `ollama list` 检查模型名是否与 `.env` 一致。
- 推荐超过 10 秒：确认 `.env` 使用三个轻量在线模型，并执行 `scripts/benchmark_fast_online.py`；8B/7B/4B 模型用于离线实验，不建议作为 8GB 显存机器的在线组合。
- 电商页面要求登录/验证码：人工完成浏览与复核，系统不得绕过；无法确认时不入库。
- 国补价不一致：地区、资格、支付方式与活动时间都会影响结算，系统只展示采集到的条件价格和时间。
