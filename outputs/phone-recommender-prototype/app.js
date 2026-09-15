const catalog = {
  "综合体验": [
    { brand: "华为", name: "HUAWEI Mate 70", token: "M", score: 92, price: 4899, platform: "拼多多官方旗舰店", gov: 4399, subsidy: 4699, reason: "影像、通信与系统体验均衡，三位模型中有两位将其列为首选。", tags: ["综合均衡", "可靠通信", "长焦影像"] },
    { brand: "小米", name: "Xiaomi 17", token: "X", score: 90, price: 4299, platform: "京东官方旗舰店", gov: 3799, subsidy: null, reason: "性能和屏幕表现突出，预算利用率较高。", tags: ["旗舰性能", "高刷屏幕"] },
    { brand: "Apple", name: "iPhone 17", token: "A", score: 88, price: 5299, platform: "天猫官方旗舰店", gov: 4799, subsidy: null, reason: "长期使用体验稳定，生态与视频能力更突出。", tags: ["系统生态", "视频能力"] }
  ],
  "摄影创作": [
    { brand: "华为", name: "HUAWEI Mate 70", token: "M", score: 95, price: 4899, platform: "拼多多官方旗舰店", gov: 4399, subsidy: 4699, reason: "多焦段覆盖更完整，色彩和长焦在模型意见中获得一致好评。", tags: ["长焦影像", "人像", "多焦段"] },
    { brand: "Apple", name: "iPhone 17", token: "A", score: 91, price: 5299, platform: "天猫官方旗舰店", gov: 4799, subsidy: null, reason: "视频录制稳定，适合持续创作。", tags: ["视频能力", "自然色彩"] },
    { brand: "vivo", name: "vivo X200 Pro", token: "V", score: 90, price: 4999, platform: "京东官方旗舰店", gov: 4499, subsidy: null, reason: "人像和长焦配置匹配摄影偏好。", tags: ["人像", "潜望长焦"] }
  ],
  "重度游戏": [
    { brand: "小米", name: "Xiaomi 17", token: "X", score: 94, price: 4299, platform: "京东官方旗舰店", gov: 3799, subsidy: null, reason: "性能释放、高刷新率与续航的组合最符合重度游戏需求。", tags: ["旗舰性能", "高刷新率", "大电池"] },
    { brand: "vivo", name: "iQOO 13", token: "Q", score: 92, price: 3799, platform: "天猫官方旗舰店", gov: 3299, subsidy: 3499, reason: "游戏调校明确，价格更有竞争力。", tags: ["电竞调校", "快充"] },
    { brand: "一加", name: "OnePlus 13", token: "O", score: 89, price: 3999, platform: "拼多多官方旗舰店", gov: 3499, subsidy: 3699, reason: "兼顾性能和日常使用，整体较均衡。", tags: ["性能释放", "流畅体验"] }
  ],
  "轻薄续航": [
    { brand: "Apple", name: "iPhone 17", token: "A", score: 93, price: 5299, platform: "天猫官方旗舰店", gov: 4799, subsidy: null, reason: "机身重量、能效和长期使用体验更贴合轻薄需求。", tags: ["轻巧", "高能效", "系统生态"] },
    { brand: "OPPO", name: "OPPO Reno 系列", token: "R", score: 89, price: 3299, platform: "京东官方旗舰店", gov: 2804, subsidy: null, reason: "握持感和充电速度更有优势。", tags: ["轻薄设计", "快充"] },
    { brand: "小米", name: "Xiaomi 17", token: "X", score: 87, price: 4299, platform: "京东官方旗舰店", gov: 3799, subsidy: null, reason: "续航和性能兼顾，适合长时间外出。", tags: ["大电池", "均衡"] }
  ]
};

const phoneSpecs = {
  "HUAWEI Mate 70": { cpu: "麒麟 9020", camera: "5000 万像素主摄", screen: "6.7 英寸 OLED", refresh: "1—120 Hz", battery: "5300 mAh", weight: "约 203 g" },
  "Xiaomi 17": { cpu: "骁龙 8 Elite 系列", camera: "5000 万像素主摄", screen: "6.3 英寸 OLED", refresh: "120 Hz", battery: "7000 mAh", weight: "约 191 g" },
  "iPhone 17": { cpu: "Apple A19", camera: "4800 万像素主摄", screen: "6.3 英寸 OLED", refresh: "120 Hz", battery: "官网未公开", weight: "约 177 g" },
  "vivo X200 Pro": { cpu: "天玑 9400", camera: "5000 万像素主摄", screen: "6.78 英寸 OLED", refresh: "120 Hz", battery: "6000 mAh", weight: "约 223 g" },
  "iQOO 13": { cpu: "骁龙 8 至尊版", camera: "5000 万像素主摄", screen: "6.82 英寸 OLED", refresh: "144 Hz", battery: "6150 mAh", weight: "约 207 g" },
  "OnePlus 13": { cpu: "骁龙 8 至尊版", camera: "5000 万像素主摄", screen: "6.82 英寸 OLED", refresh: "120 Hz", battery: "6000 mAh", weight: "约 210 g" },
  "OPPO Reno 系列": { cpu: "天玑系列处理器", camera: "5000 万像素主摄", screen: "约 6.6 英寸 OLED", refresh: "120 Hz", battery: "约 6000 mAh", weight: "约 190 g" }
};

let currentResults = catalog["综合体验"];

const money = value => value ? `¥${value.toLocaleString("zh-CN")}` : "暂无";
const toast = document.querySelector("#toast");
let toastTimer;
function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

function renderResults(items, usage, budget) {
  currentResults = items;
  const top = items[0];
  document.querySelector("#result-title").textContent = `预算 ${money(budget)} · ${usage}优先`;
  document.querySelector("#hero-token").textContent = top.token;
  document.querySelector("#hero-brand").textContent = top.brand;
  document.querySelector("#hero-name").textContent = top.name;
  document.querySelector("#hero-score").innerHTML = `${top.score}<span>分</span>`;
  document.querySelector("#hero-reason").textContent = top.reason;
  document.querySelector("#hero-price").textContent = money(top.price);
  document.querySelector("#hero-platform").textContent = top.platform;
  document.querySelector("#hero-gov").textContent = `国补参考 ${money(top.gov)}`;
  document.querySelector("#hero-subsidy").textContent = top.subsidy ? `百亿补贴 ${money(top.subsidy)}` : "百亿补贴 暂无";
  document.querySelector("#hero-tags").innerHTML = top.tags.map(tag => `<span>${tag}</span>`).join("");
  document.querySelector("#secondary-results").innerHTML = items.slice(1).map((item, index) => `
    <article class="secondary-card clickable-card" role="button" tabindex="0" data-result-index="${index + 1}" aria-label="查看 ${item.name} 基本参数">
      <span class="number">0${index + 2}</span>
      <div><h4>${item.name}</h4><p>${item.reason}</p></div>
      <b>${money(item.price)}<small>${item.score} 分</small></b>
    </article>
  `).join("");
}

const modal = document.querySelector("#spec-modal");
function showSpecs(phone) {
  const specs = phoneSpecs[phone.name] || { cpu: "待数据库补充", camera: "待数据库补充", screen: "待数据库补充", refresh: "待数据库补充", battery: "待数据库补充", weight: "待数据库补充" };
  document.querySelector("#spec-token").textContent = phone.token;
  document.querySelector("#spec-brand").textContent = phone.brand;
  document.querySelector("#spec-name").textContent = phone.name;
  document.querySelector("#spec-cpu").textContent = specs.cpu;
  document.querySelector("#spec-camera").textContent = specs.camera;
  document.querySelector("#spec-screen").textContent = specs.screen;
  document.querySelector("#spec-refresh").textContent = specs.refresh;
  document.querySelector("#spec-battery").textContent = specs.battery;
  document.querySelector("#spec-weight").textContent = specs.weight;
  modal.hidden = false;
  document.body.classList.add("modal-open");
  modal.querySelector(".modal-close").focus();
}

function closeSpecs() {
  modal.hidden = true;
  document.body.classList.remove("modal-open");
}

document.querySelector("#hero-result").addEventListener("click", () => showSpecs(currentResults[0]));
document.querySelector("#hero-result").addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); showSpecs(currentResults[0]); }
});
document.querySelector("#secondary-results").addEventListener("click", event => {
  const card = event.target.closest("[data-result-index]");
  if (card) showSpecs(currentResults[Number(card.dataset.resultIndex)]);
});
document.querySelector("#secondary-results").addEventListener("keydown", event => {
  const card = event.target.closest("[data-result-index]");
  if (card && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); showSpecs(currentResults[Number(card.dataset.resultIndex)]); }
});
document.querySelectorAll("[data-close-modal]").forEach(element => element.addEventListener("click", closeSpecs));
document.addEventListener("keydown", event => { if (event.key === "Escape" && !modal.hidden) closeSpecs(); });

document.querySelectorAll("[data-budget]").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelector("#budget").value = button.dataset.budget;
    document.querySelectorAll("[data-budget]").forEach(item => item.classList.toggle("selected", item === button));
  });
});

document.querySelector("#recommend-form").addEventListener("submit", event => {
  event.preventDefault();
  const usage = new FormData(event.currentTarget).get("usage");
  const budget = Number(document.querySelector("#budget").value || 5000);
  let matches = catalog[usage].filter(item => item.price <= budget + 800);
  if (matches.length < 3) matches = catalog[usage];
  renderResults(matches.slice(0, 3), usage, budget);
  document.querySelector("#evidence-drawer").hidden = true;
  showToast("已融合 3 个模型意见并刷新推荐");
});

document.querySelector("#explain-button").addEventListener("click", event => {
  const drawer = document.querySelector("#evidence-drawer");
  drawer.hidden = !drawer.hidden;
  event.currentTarget.textContent = drawer.hidden ? "查看融合解释" : "收起融合解释";
});

document.querySelectorAll(".nav-item").forEach(button => {
  button.addEventListener("click", () => {
    if (button.dataset.page !== "推荐") {
      showToast(`${button.dataset.page}将在接入 SQLite 后启用`);
      return;
    }
    document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item === button));
  });
});

document.querySelector("#manage-button").addEventListener("click", () => showToast("下一阶段将实现数据库增删改查页面"));
renderResults(catalog["综合体验"], "综合体验", 5000);
