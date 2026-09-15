# 数据导出说明

生成命令：`python scripts/export_data.py --output outputs/data-export-v1.7`

- `phones.csv`：203 款手机的完整参数、来源和核验时间；
- `variants.csv`：394 个内存版本及发售价来源；
- `listings.csv`：全部平台商品记录，包含已停用的早期演示记录，需结合 `is_active` 与 `store_verified` 判断；
- `price_snapshots.csv`：全部历史价格快照，包含 `demo` 与 `reviewed` 状态，便于审计；
- `reviewed_market_prices.csv`：仅保留“商品启用、官方店已核验、价格已复核”的真实价格快照，适合展示与统计；
- `phone_recommender.backup.db`：完整 SQLite 备份，因体积和数据边界不提交 GitHub，可在本机重新生成。

未知字段保持空值。国补百分比不会自动折算为确定价格，实际资格与结算价由用户在平台页面确认。
