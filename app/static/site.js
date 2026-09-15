const toast = document.querySelector("#toast");
let toastTimer;
function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

document.querySelectorAll("[data-budget]").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelector("#budget").value = button.dataset.budget;
    document.querySelectorAll("[data-budget]").forEach(item => item.classList.toggle("selected", item === button));
  });
});

document.querySelectorAll("[data-toggle]").forEach(button => {
  button.addEventListener("click", () => {
    const target = document.querySelector(button.dataset.toggle);
    if (target) target.hidden = !target.hidden;
  });
});

const recommendForm = document.querySelector("#recommend-form");
if (recommendForm) {
  recommendForm.addEventListener("submit", () => {
    const button = document.querySelector("#submit-recommend");
    button.disabled = true;
    button.querySelector("span").textContent = "正在依次调用三个模型，请稍候…";
    showToast("本地模型首次加载可能需要几十秒");
  });
}

const modal = document.querySelector("#spec-modal");
function openSpecs(card) {
  if (!modal) return;
  const spec = JSON.parse(card.dataset.spec);
  const values = {
    "#spec-token": (spec.brand || "机")[0], "#spec-brand": spec.brand, "#spec-name": spec.name,
    "#spec-variant": `${spec.variant || ""} · 基本参数速览`, "#spec-cpu": spec.cpu || "待补充",
    "#spec-camera": spec.camera || "待补充", "#spec-screen": spec.screen || "待补充",
    "#spec-refresh": spec.refresh || "待补充", "#spec-battery": spec.battery || "待补充", "#spec-weight": spec.weight || "待补充"
  };
  Object.entries(values).forEach(([selector, value]) => document.querySelector(selector).textContent = value);
  const source = document.querySelector("#spec-source");
  source.href = spec.source || "#";
  source.hidden = !spec.source;
  modal.hidden = false;
  document.body.classList.add("modal-open");
  modal.querySelector(".modal-close").focus();
}
function closeSpecs() { if (modal) modal.hidden = true; document.body.classList.remove("modal-open"); }
document.querySelectorAll(".spec-trigger").forEach(card => {
  card.addEventListener("click", () => openSpecs(card));
  card.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); openSpecs(card); } });
});
document.querySelectorAll("[data-close-modal]").forEach(item => item.addEventListener("click", closeSpecs));
document.addEventListener("keydown", event => { if (event.key === "Escape") closeSpecs(); });

document.querySelectorAll("img[data-official-image]").forEach(image => {
  image.addEventListener("error", () => {
    const replacement = document.createElement("span");
    replacement.className = "official-image-error";
    replacement.textContent = "官网图片暂时无法加载";
    image.replaceWith(replacement);
  }, { once: true });
});

document.querySelectorAll("[data-recommendation-stack]").forEach(stack => {
  const cards = [...stack.querySelectorAll("[data-recommendation-card]")];
  const activateCard = activeCard => {
    cards.forEach(card => {
      const isActive = card === activeCard;
      card.classList.toggle("is-expanded", isActive);
      card.setAttribute("aria-expanded", String(isActive));
    });
  };

  cards.forEach(card => {
    card.addEventListener("mouseenter", () => activateCard(card));
    card.addEventListener("focusin", () => activateCard(card));
    card.addEventListener("click", event => {
      if (!event.target.closest("a, button")) activateCard(card);
    });
  });
});
