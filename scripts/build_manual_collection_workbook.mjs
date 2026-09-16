import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = process.cwd();
const outputDir = path.join(projectRoot, "outputs", "manual-collection-20260916");
const outputPath = path.join(outputDir, "手机数据人工采集模板.xlsx");
const previewDir = path.join(outputDir, "preview");

const COLORS = {
  dark: "#173F35",
  dark2: "#225B4B",
  mid: "#DCEBE5",
  light: "#F5F8F6",
  input: "#FFF2CC",
  line: "#CAD6D0",
  muted: "#5E6D67",
  red: "#FCE8E6",
  redText: "#B3261E",
  green: "#E6F4EA",
  greenText: "#137333",
  gray: "#ECEFF1",
  blue: "#E8F0FE",
};
const FONT = "Arial";

async function csvValues(relativePath) {
  const csv = (await fs.readFile(path.join(projectRoot, relativePath), "utf8")).replace(/^\uFEFF/, "");
  const imported = await Workbook.fromCSV(csv, { sheetName: "Raw" });
  return imported.worksheets.getItem("Raw").getUsedRange().values;
}

function objectsFromRows(rows) {
  const headers = rows[0].map(String);
  return rows.slice(1).filter((row) => row.some((value) => value !== null && value !== "")).map((row) =>
    Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""]))
  );
}

function num(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function bool(value) {
  const text = String(value ?? "").trim().toLowerCase();
  if (!text) return null;
  return ["true", "1", "yes"].includes(text);
}

function dateOnly(value) {
  const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function baseSheet(sheet, title, sourceText, lastColumn) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 8;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: FONT, size: 15, bold: true, color: COLORS.dark };
  sheet.getRange(`A3:${lastColumn}3`).format.borders = { bottom: { style: "thin", color: COLORS.line } };
  sheet.getRange("A3").values = [[sourceText]];
  sheet.getRange("A3").format.font = { name: FONT, size: 9, italic: true, color: COLORS.muted };
  sheet.getRange(`A1:${lastColumn}2000`).format.verticalAlignment = "center";
  sheet.getRange(`A1:${lastColumn}2000`).format.font.name = FONT;
}

function styleHeader(sheet, rangeAddress) {
  const header = sheet.getRange(rangeAddress);
  header.format.fill = COLORS.dark;
  header.format.font = { name: FONT, size: 10, bold: true, color: "#FFFFFF" };
  header.format.horizontalAlignment = "center";
  header.format.verticalAlignment = "center";
  header.format.wrapText = true;
  header.format.rowHeight = 32;
  header.format.borders = { preset: "inside", style: "thin", color: "#FFFFFF" };
}

function addStatusFormatting(range) {
  range.conditionalFormats.add("containsText", { text: "待补充", format: { fill: COLORS.red, font: { color: COLORS.redText, bold: true } } });
  range.conditionalFormats.add("containsText", { text: "可提交", format: { fill: COLORS.green, font: { color: COLORS.greenText, bold: true } } });
  range.conditionalFormats.add("containsText", { text: "基本完整", format: { fill: COLORS.green, font: { color: COLORS.greenText, bold: true } } });
  range.conditionalFormats.add("containsText", { text: "无法确认", format: { fill: COLORS.gray, font: { color: COLORS.muted, bold: true } } });
}

function setWidths(sheet, widthMap) {
  for (const [column, width] of Object.entries(widthMap)) {
    sheet.getRange(`${column}:${column}`).format.columnWidth = width;
  }
}

const phoneRows = objectsFromRows(await csvValues("outputs/data-export-v1.7/phones.csv"));
const variantRows = objectsFromRows(await csvValues("outputs/data-export-v1.7/variants.csv"));
const priceRows = objectsFromRows(await csvValues("data/review/price_review_queue.csv"));
const whitelistRows = objectsFromRows(await csvValues("config/official_store_whitelist.csv"));

const workbook = Workbook.create();
const guide = workbook.worksheets.add("填写说明");
const prices = workbook.worksheets.add("三平台价格");
const phones = workbook.worksheets.add("手机参数");
const variants = workbook.worksheets.add("内存版本");
const whitelist = workbook.worksheets.add("官方店白名单");

// Guide / summary
guide.showGridLines = false;
guide.tabColor = COLORS.dark;
guide.getRange("A2").values = [["手机数据人工采集工作簿"]];
guide.getRange("A2").format.font = { name: FONT, size: 16, bold: true, color: COLORS.dark };
guide.getRange("A3:H3").format.borders = { bottom: { style: "thin", color: COLORS.line } };
guide.getRange("A4").values = [["填写目标"]];
guide.getRange("A4").format.font = { name: FONT, size: 12, bold: true, color: COLORS.dark };
guide.getRange("A5:B12").values = [
  ["项目", "当前数量"],
  ["三平台价格任务", null],
  ["价格任务可提交", null],
  ["价格任务待补充", null],
  ["手机型号", null],
  ["手机参数待补充", null],
  ["内存版本", null],
  ["版本发售价待补充", null],
];
guide.getRange("B6").formulas = [[`=COUNTA('三平台价格'!$A$6:$A$${priceRows.length + 5})`]];
guide.getRange("B7").formulas = [[`=COUNTIF('三平台价格'!$AC$6:$AC$${priceRows.length + 5},"可提交")`]];
guide.getRange("B8").formulas = [[`=COUNTIF('三平台价格'!$AC$6:$AC$${priceRows.length + 5},"待补充")`]];
guide.getRange("B9").formulas = [[`=COUNTA('手机参数'!$A$6:$A$${phoneRows.length + 5})`]];
guide.getRange("B10").formulas = [[`=COUNTIF('手机参数'!$AC$6:$AC$${phoneRows.length + 5},"待补充")`]];
guide.getRange("B11").formulas = [[`=COUNTA('内存版本'!$A$6:$A$${variantRows.length + 5})`]];
guide.getRange("B12").formulas = [[`=COUNTIF('内存版本'!$P$6:$P$${variantRows.length + 5},"待补充")`]];
guide.getRange("A5:B5").format = { fill: COLORS.dark, font: { name: FONT, bold: true, color: "#FFFFFF" } };
guide.getRange("A6:A12").format.fill = COLORS.light;
guide.getRange("B6:B12").format = { fill: COLORS.mid, font: { name: FONT, bold: true, color: COLORS.dark }, numberFormat: "#,##0" };
guide.getRange("D4").values = [["操作顺序"]];
guide.getRange("D4").format.font = { name: FONT, size: 12, bold: true, color: COLORS.dark };
guide.getRange("D5:H10").values = [
  ["1", "先查看“官方店白名单”，确认平台、品牌和店铺精确名称。", null, null, null],
  ["2", "在“三平台价格”中筛选负责人、品牌和平台，逐行打开搜索入口。", null, null, null],
  ["3", "只填写官方旗舰店或官方自营旗舰店的商品直达页；型号与内存版本必须一致。", null, null, null],
  ["4", "价格填写纯数字，单位为元。没有明确显示的国补或百亿补贴留空，禁止按百分比推算。", null, null, null],
  ["5", "每个商品页保存截图，文件名使用任务ID，例如 PRICE-00001.png。", null, null, null],
  ["6", "完成后不要删除任务ID、不要改工作表名称；将 Excel 与截图文件夹一起交回。", null, null, null],
];
for (let row = 5; row <= 10; row += 1) guide.mergeCells(`E${row}:H${row}`);
guide.getRange("D5:H10").format.wrapText = true;
guide.getRange("D5:D10").format = { fill: COLORS.mid, font: { name: FONT, bold: true, color: COLORS.dark }, horizontalAlignment: "center" };
guide.getRange("E5:H10").format.fill = COLORS.light;
guide.getRange("D5:H10").format.borders = { preset: "outside", style: "thin", color: COLORS.line };
guide.getRange("D5:H10").format.rowHeight = 48;
guide.getRange("A15").values = [["填写规则"]];
guide.getRange("A15").format.font = { name: FONT, size: 12, bold: true, color: COLORS.dark };
guide.getRange("A16:H23").values = [
  ["黄色单元格", "需要人工填写或复核；已有内容可保留，发现错误时可修正。", null, null, null, null, null, null],
  ["灰色单元格", "系统预填的任务标识和参考字段，尽量不要修改。", null, null, null, null, null, null],
  ["价格", "使用页面当前公开展示的价格，不填 0，不把会员券、以旧换新或账号专享价当作通用售价。", null, null, null, null, null, null],
  ["国补价格", "只有页面明确显示确定金额时才填“实显国补价”；资格由最终购买者自行判断。", null, null, null, null, null, null],
  ["官方店", "店名必须逐字记录；新店同时补充到“官方店白名单”并填写店铺主页。", null, null, null, null, null, null],
  ["商品链接", "填写商品详情直达链接，不填写搜索结果页、分享中转页或第三方店铺链接。", null, null, null, null, null, null],
  ["无法确认", "遇到验证码、下架、无对应版本或无法确认官方店时选择“无法确认”，并在备注写原因。", null, null, null, null, null, null],
  ["数据回传", "不要重命名工作表。允许筛选和排序，但不得删除任务ID、手机ID或版本ID。", null, null, null, null, null, null],
];
for (let row = 16; row <= 23; row += 1) guide.mergeCells(`B${row}:H${row}`);
guide.getRange("A16:A23").format = { fill: COLORS.mid, font: { name: FONT, bold: true, color: COLORS.dark } };
guide.getRange("B16:H23").format = { fill: COLORS.light, wrapText: true };
guide.getRange("A16:H23").format.borders = { preset: "outside", style: "thin", color: COLORS.line };
guide.getRange("A1:H30").format.font.name = FONT;
guide.getRange("A:A").format.columnWidth = 18;
guide.getRange("B:B").format.columnWidth = 22;
guide.getRange("C:C").format.columnWidth = 3;
guide.getRange("D:D").format.columnWidth = 6;
guide.getRange("E:H").format.columnWidth = 18;
guide.getRange("A16:H23").format.rowHeight = 40;

// Price collection sheet
baseSheet(prices, "三平台价格人工采集任务", "数据来源：项目价格审核队列 data/review/price_review_queue.csv；黄色列由采集人填写或复核。", "AC");
prices.tabColor = COLORS.dark2;
const priceHeaders = ["任务ID", "优先级", "发布日期", "品牌", "型号", "RAM(GB)", "存储(GB)", "内存版本", "平台", "发售价参考", "搜索入口", "官方店精确名称", "商品ID", "SKU文字", "商品标题", "商品直达链接", "常规价", "公开活动价", "实显国补价", "百亿补贴价", "促销标签", "优惠叠加", "当前有货", "采集日期", "采集人", "证据文件名", "采集状态", "备注", "完整性检查"];
prices.getRange("A5:AC5").values = [priceHeaders];
const priceData = priceRows.map((row, index) => {
  const evidence = String(row.screenshot_path || row.evidence_text_path || "").split(/[\\/]/).pop();
  const status = row.review_status === "approved" ? "已完成" : row.review_status === "blocked" ? "无法确认" : "未开始";
  return [
    `PRICE-${String(index + 1).padStart(5, "0")}`, row.priority || "NORMAL", dateOnly(row.release_date), row.brand, row.model_name,
    num(row.ram_gb), num(row.storage_gb), row.variant_name, row.platform, num(row.launch_price), row.search_url,
    row.store_name, row.external_id, row.sku_text, row.product_title, row.product_url,
    num(row.regular_price), num(row.public_sale_price), num(row.gov_price), num(row.billion_subsidy_price), row.promotion_labels,
    row.promotion_stackable || "unknown", String(row.in_stock || "").toLowerCase() === "true" ? "是" : String(row.in_stock || "").toLowerCase() === "false" ? "否" : "", dateOnly(row.captured_at), row.reviewer, evidence,
    status, row.capture_note || "", null,
  ];
});
prices.getRange(`A6:AC${priceRows.length + 5}`).values = priceData;
prices.getRange(`AC6:AC${priceRows.length + 5}`).formulasR1C1 = priceRows.map(() => [
  '=IF(RC[-2]="无法确认","无法确认",IF(AND(RC[-17]<>"",RC[-16]<>"",RC[-15]<>"",RC[-13]<>"",RC[-6]<>"",RC[-4]<>"",RC[-3]<>"",OR(RC[-12]<>"",RC[-11]<>"",RC[-10]<>"",RC[-9]<>"")),"可提交","待补充"))'
]);
styleHeader(prices, "A5:AC5");
prices.getRange(`A6:K${priceRows.length + 5}`).format.fill = COLORS.gray;
prices.getRange(`L6:AB${priceRows.length + 5}`).format.fill = COLORS.input;
prices.getRange(`AC6:AC${priceRows.length + 5}`).format.fill = COLORS.light;
prices.getRange(`C6:C${priceRows.length + 5}`).setNumberFormat("yyyy-mm-dd");
prices.getRange(`J6:J${priceRows.length + 5}`).setNumberFormat('¥#,##0');
prices.getRange(`Q6:T${priceRows.length + 5}`).setNumberFormat('¥#,##0.00');
prices.getRange(`X6:X${priceRows.length + 5}`).setNumberFormat("yyyy-mm-dd");
prices.getRange(`V6:V${priceRows.length + 5}`).dataValidation = { rule: { type: "list", values: ["unknown", "yes", "no"] } };
prices.getRange(`W6:W${priceRows.length + 5}`).dataValidation = { rule: { type: "list", values: ["是", "否"] } };
prices.getRange(`AA6:AA${priceRows.length + 5}`).dataValidation = { rule: { type: "list", values: ["未开始", "采集中", "已完成", "无法确认"] } };
addStatusFormatting(prices.getRange(`AC6:AC${priceRows.length + 5}`));
prices.getRange(`A5:AC${priceRows.length + 5}`).format.borders = { insideHorizontal: { style: "thin", color: COLORS.line } };
prices.getRange(`A5:AC${priceRows.length + 5}`).format.rowHeight = 22;
prices.getRange(`K6:P${priceRows.length + 5}`).format.wrapText = false;
setWidths(prices, { A: 15, B: 10, C: 12, D: 10, E: 22, F: 9, G: 10, H: 14, I: 8, J: 12, K: 30, L: 24, M: 17, N: 16, O: 34, P: 38, Q: 12, R: 13, S: 13, T: 13, U: 24, V: 11, W: 10, X: 12, Y: 12, Z: 24, AA: 12, AB: 28, AC: 13 });
prices.freezePanes.freezeRows(5);
prices.freezePanes.freezeColumns(5);
const priceTable = prices.tables.add(`A5:AC${priceRows.length + 5}`, true, "PriceCollectionTable");
priceTable.style = "TableStyleMedium4";

// Phone parameter sheet
baseSheet(phones, "手机参数复核与补充", "数据来源：项目 SQLite 导出 outputs/data-export-v1.7/phones.csv；空白核心字段需要从品牌官网补充。", "AC");
phones.tabColor = "#4F7668";
const phoneHeaders = ["手机ID", "品牌", "型号", "发布日期", "销售状态", "CPU", "屏幕尺寸(英寸)", "屏幕类型", "分辨率", "刷新率(Hz)", "主摄(MP)", "相机说明", "电池(mAh)", "有线充电(W)", "无线充电(W)", "重量(g)", "厚度(mm)", "防水等级", "操作系统", "官网图片URL", "图片来源页", "官网参数页", "来源核验日期", "数据质量", "采集人", "采集日期", "采集状态", "备注", "完整性检查"];
phones.getRange("A5:AC5").values = [phoneHeaders];
const phoneData = phoneRows.map((row) => {
  const coreComplete = [row.release_date, row.cpu, row.screen_size, row.screen_type, row.refresh_rate, row.main_camera_mp, row.battery_mah, row.weight_g, row.image_url, row.image_source_url, row.official_url].every((value) => String(value ?? "").trim());
  return [
    num(row.id), row.brand, row.model_name, dateOnly(row.release_date), row.sale_status, row.cpu, num(row.screen_size), row.screen_type,
    row.resolution, num(row.refresh_rate), num(row.main_camera_mp), row.camera_summary, num(row.battery_mah), num(row.charging_w),
    num(row.wireless_charging_w), num(row.weight_g), num(row.thickness_mm), row.waterproof, row.operating_system,
    row.image_url, row.image_source_url, row.official_url || row.source_url, dateOnly(row.source_checked_at), row.data_quality,
    "", null, coreComplete ? "无需处理" : "未开始", "", null,
  ];
});
phones.getRange(`A6:AC${phoneRows.length + 5}`).values = phoneData;
phones.getRange(`AC6:AC${phoneRows.length + 5}`).formulasR1C1 = phoneRows.map(() => [
  '=IF(RC[-2]="无法确认","无法确认",IF(AND(RC[-25]<>"",RC[-23]<>"",RC[-22]<>"",RC[-21]<>"",RC[-19]<>"",RC[-18]<>"",RC[-16]<>"",RC[-13]<>"",RC[-9]<>"",RC[-8]<>"",RC[-7]<>""),"基本完整","待补充"))'
]);
styleHeader(phones, "A5:AC5");
phones.getRange(`A6:C${phoneRows.length + 5}`).format.fill = COLORS.gray;
phones.getRange(`D6:AB${phoneRows.length + 5}`).format.fill = COLORS.input;
phones.getRange(`AC6:AC${phoneRows.length + 5}`).format.fill = COLORS.light;
phones.getRange(`D6:D${phoneRows.length + 5}`).setNumberFormat("yyyy-mm-dd");
phones.getRange(`W6:W${phoneRows.length + 5}`).setNumberFormat("yyyy-mm-dd");
phones.getRange(`Z6:Z${phoneRows.length + 5}`).setNumberFormat("yyyy-mm-dd");
phones.getRange(`E6:E${phoneRows.length + 5}`).dataValidation = { rule: { type: "list", values: ["on_sale", "partial_sale", "out_of_stock", "discontinued"] } };
phones.getRange(`AA6:AA${phoneRows.length + 5}`).dataValidation = { rule: { type: "list", values: ["未开始", "采集中", "无需处理", "已完成", "无法确认"] } };
addStatusFormatting(phones.getRange(`AC6:AC${phoneRows.length + 5}`));
phones.getRange(`A5:AC${phoneRows.length + 5}`).format.borders = { insideHorizontal: { style: "thin", color: COLORS.line } };
phones.getRange(`A5:AC${phoneRows.length + 5}`).format.rowHeight = 22;
setWidths(phones, { A: 10, B: 10, C: 24, D: 12, E: 14, F: 24, G: 13, H: 14, I: 18, J: 12, K: 11, L: 30, M: 12, N: 12, O: 13, P: 11, Q: 11, R: 12, S: 18, T: 38, U: 38, V: 38, W: 14, X: 19, Y: 12, Z: 12, AA: 12, AB: 28, AC: 13 });
phones.freezePanes.freezeRows(5);
phones.freezePanes.freezeColumns(3);
const phoneTable = phones.tables.add(`A5:AC${phoneRows.length + 5}`, true, "PhoneParameterTable");
phoneTable.style = "TableStyleMedium4";

// Variant sheet
baseSheet(variants, "内存版本与发售价复核", "数据来源：项目 SQLite 导出 outputs/data-export-v1.7/variants.csv；每个内存版本独立核验发售价及来源。", "P");
variants.tabColor = "#789B8E";
const variantHeaders = ["版本ID", "手机ID", "品牌", "型号", "RAM(GB)", "存储(GB)", "版本名称", "发售价", "发售价来源", "限量配色", "当前有效", "采集人", "采集日期", "采集状态", "备注", "完整性检查"];
variants.getRange("A5:P5").values = [variantHeaders];
const variantData = variantRows.map((row) => [
  num(row.id), num(row.model_id), row.brand, row.model_name, num(row.ram_gb), num(row.storage_gb), row.variant_name,
  num(row.launch_price), row.launch_price_source, bool(row.color_limited), bool(row.is_active), "", null,
  row.launch_price && row.launch_price_source ? "无需处理" : "未开始", "", null,
]);
variants.getRange(`A6:P${variantRows.length + 5}`).values = variantData;
variants.getRange(`P6:P${variantRows.length + 5}`).formulasR1C1 = variantRows.map(() => [
  '=IF(RC[-2]="无法确认","无法确认",IF(AND(RC[-8]<>"",RC[-7]<>""),"基本完整","待补充"))'
]);
styleHeader(variants, "A5:P5");
variants.getRange(`A6:G${variantRows.length + 5}`).format.fill = COLORS.gray;
variants.getRange(`H6:O${variantRows.length + 5}`).format.fill = COLORS.input;
variants.getRange(`P6:P${variantRows.length + 5}`).format.fill = COLORS.light;
variants.getRange(`H6:H${variantRows.length + 5}`).setNumberFormat('¥#,##0');
variants.getRange(`M6:M${variantRows.length + 5}`).setNumberFormat("yyyy-mm-dd");
variants.getRange(`N6:N${variantRows.length + 5}`).dataValidation = { rule: { type: "list", values: ["未开始", "采集中", "无需处理", "已完成", "无法确认"] } };
addStatusFormatting(variants.getRange(`P6:P${variantRows.length + 5}`));
variants.getRange(`A5:P${variantRows.length + 5}`).format.borders = { insideHorizontal: { style: "thin", color: COLORS.line } };
variants.getRange(`A5:P${variantRows.length + 5}`).format.rowHeight = 22;
setWidths(variants, { A: 10, B: 10, C: 10, D: 24, E: 10, F: 11, G: 15, H: 12, I: 40, J: 11, K: 11, L: 12, M: 12, N: 12, O: 28, P: 13 });
variants.freezePanes.freezeRows(5);
variants.freezePanes.freezeColumns(4);
const variantTable = variants.tables.add(`A5:P${variantRows.length + 5}`, true, "VariantCollectionTable");
variantTable.style = "TableStyleMedium4";

// Official-store whitelist and candidates
baseSheet(whitelist, "官方店白名单与新增候选", "数据来源：config/official_store_whitelist.csv；新增店铺必须填写精确店名、店铺主页、核验日期和核验人。", "I");
whitelist.tabColor = "#A8BDB5";
const whitelistHeaders = ["平台", "品牌", "官方店精确名称", "店铺主页", "核验日期", "核验说明", "核验人", "状态", "完整性检查"];
whitelist.getRange("A5:I5").values = [whitelistHeaders];
const whitelistTotalRows = 60;
const whitelistData = Array.from({ length: whitelistTotalRows }, (_, index) => {
  const row = whitelistRows[index];
  if (!row) return ["", "", "", "", null, "", "", "待补充", null];
  return [row.platform, row.brand, row.store_name, row.store_url, dateOnly(row.verified_at), row.verification_note, "系统已有审核", "已核验", null];
});
whitelist.getRange(`A6:I${whitelistTotalRows + 5}`).values = whitelistData;
whitelist.getRange(`I6:I${whitelistTotalRows + 5}`).formulasR1C1 = whitelistData.map(() => [
  '=IF(RC[-1]="已核验","可使用",IF(AND(RC[-8]<>"",RC[-7]<>"",RC[-6]<>"",RC[-5]<>"",RC[-4]<>"",RC[-2]<>""),"可提交","待补充"))'
]);
styleHeader(whitelist, "A5:I5");
whitelist.getRange(`A6:H${whitelistTotalRows + 5}`).format.fill = COLORS.input;
whitelist.getRange(`I6:I${whitelistTotalRows + 5}`).format.fill = COLORS.light;
whitelist.getRange(`E6:E${whitelistTotalRows + 5}`).setNumberFormat("yyyy-mm-dd");
whitelist.getRange(`A6:A${whitelistTotalRows + 5}`).dataValidation = { rule: { type: "list", values: ["jd", "tmall", "pdd"] } };
whitelist.getRange(`H6:H${whitelistTotalRows + 5}`).dataValidation = { rule: { type: "list", values: ["待补充", "待审核", "已核验", "拒绝"] } };
addStatusFormatting(whitelist.getRange(`I6:I${whitelistTotalRows + 5}`));
whitelist.getRange(`A5:I${whitelistTotalRows + 5}`).format.borders = { insideHorizontal: { style: "thin", color: COLORS.line } };
whitelist.getRange(`A5:I${whitelistTotalRows + 5}`).format.rowHeight = 23;
setWidths(whitelist, { A: 10, B: 12, C: 28, D: 42, E: 13, F: 36, G: 14, H: 12, I: 13 });
whitelist.freezePanes.freezeRows(5);
const whitelistTable = whitelist.tables.add(`A5:I${whitelistTotalRows + 5}`, true, "OfficialStoreTable");
whitelistTable.style = "TableStyleMedium4";

workbook.recalculate();

// Disposable workflow check: a completed row must become submittable, while
// an explicit blocker must remain visible. Restore the original row afterward.
const originalPriceInputs = prices.getRange("L6:AB6").values;
prices.getRange("L6:AB6").values = [[
  "测试官方旗舰店", "TEST-001", "12GB+512GB", "测试商品标题", "https://item.jd.com/100000000001.html",
  4999, 4699, null, null, "公开活动价", "unknown", "是", new Date(2026, 8, 16), "测试采集人",
  "PRICE-00001.png", "已完成", "临时验证行",
]];
workbook.recalculate();
if (prices.getRange("AC6").values[0][0] !== "可提交") throw new Error("价格任务完整性公式未在完整输入后返回“可提交”");
prices.getRange("AA6").values = [["无法确认"]];
workbook.recalculate();
if (prices.getRange("AC6").values[0][0] !== "无法确认") throw new Error("价格任务完整性公式未保留阻断状态");
prices.getRange("L6:AB6").values = originalPriceInputs;
workbook.recalculate();

await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range] of [
  ["填写说明", "A1:H23"],
  ["三平台价格", "A1:AC18"],
  ["手机参数", "A1:AC18"],
  ["内存版本", "A1:P18"],
  ["官方店白名单", "A1:I20"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const summary = await workbook.inspect({
  kind: "table",
  range: "填写说明!A1:H23",
  include: "values,formulas",
  tableMaxRows: 23,
  tableMaxCols: 8,
});
console.log(summary.ndjson);
const sample = await workbook.inspect({
  kind: "table",
  range: "三平台价格!A5:AC8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 29,
});
console.log(sample.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const reopenedSummary = await reopened.inspect({
  kind: "sheet,table",
  maxChars: 4000,
  tableMaxRows: 3,
  tableMaxCols: 6,
});
console.log(reopenedSummary.ndjson);
const reopenedErrors = await reopened.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "reopened workbook formula error scan",
});
console.log(reopenedErrors.ndjson);
console.log(JSON.stringify({ outputPath, counts: { phones: phoneRows.length, variants: variantRows.length, prices: priceRows.length, whitelist: whitelistRows.length } }));
