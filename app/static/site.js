const toast = document.querySelector("#toast");
let toastTimer;
function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

const REACTION_VISITOR_KEY = "phone-recommender-visitor-v1";
const LIKED_PHONES_KEY = "phone-recommender-liked-v1";
const DISLIKED_PHONES_KEY = "phone-recommender-disliked-v1";

function getVisitorId() {
  try {
    const existing = localStorage.getItem(REACTION_VISITOR_KEY);
    if (existing) return existing;
    const generated = crypto.randomUUID
      ? crypto.randomUUID()
      : [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, "0")).join("");
    localStorage.setItem(REACTION_VISITOR_KEY, generated);
    return generated;
  } catch {
    return `visitor-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
}

function readPhoneSet(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(value) ? value.map(String) : []);
  } catch {
    return new Set();
  }
}

function writePhoneSet(key, phoneIds) {
  try {
    localStorage.setItem(key, JSON.stringify([...phoneIds]));
  } catch {
    // The server count still works when local storage is unavailable.
  }
}

function applyLikeState(button, liked, count) {
  button.classList.toggle("is-liked", liked);
  button.setAttribute("aria-pressed", String(liked));
  const label = button.dataset.reactionLabel || "这款手机";
  button.setAttribute("aria-label", `${liked ? "取消点赞" : "点赞"} ${label}`);
  const heart = button.querySelector(".reaction-heart");
  const countNode = button.querySelector(".like-count");
  if (heart) heart.textContent = liked ? "♥" : "♡";
  if (countNode) countNode.textContent = Number(count || 0).toLocaleString("zh-CN");
}

function applyDislikeState(button, disliked, count) {
  button.classList.toggle("is-disliked", disliked);
  button.setAttribute("aria-pressed", String(disliked));
  const label = button.dataset.reactionLabel || "这款手机";
  button.setAttribute("aria-label", `${disliked ? "取消踩" : "踩"} ${label}`);
  const countNode = button.querySelector(".dislike-count");
  if (countNode) countNode.textContent = Number(count || 0).toLocaleString("zh-CN");
}

const likedPhones = readPhoneSet(LIKED_PHONES_KEY);
const dislikedPhones = readPhoneSet(DISLIKED_PHONES_KEY);
likedPhones.forEach(phoneId => {
  if (dislikedPhones.has(phoneId)) likedPhones.delete(phoneId);
});

function applyReactionPayload(phoneId, payload) {
  if (payload.liked) {
    likedPhones.add(phoneId);
    dislikedPhones.delete(phoneId);
  } else {
    likedPhones.delete(phoneId);
  }
  if (payload.disliked) {
    dislikedPhones.add(phoneId);
    likedPhones.delete(phoneId);
  } else {
    dislikedPhones.delete(phoneId);
  }
  writePhoneSet(LIKED_PHONES_KEY, likedPhones);
  writePhoneSet(DISLIKED_PHONES_KEY, dislikedPhones);
  document.querySelectorAll("[data-like-phone]").forEach(button => {
    if (String(button.dataset.likePhone) === phoneId) {
      applyLikeState(button, Boolean(payload.liked), payload.like_count);
    }
  });
  document.querySelectorAll("[data-dislike-phone]").forEach(button => {
    if (String(button.dataset.dislikePhone) === phoneId) {
      applyDislikeState(button, Boolean(payload.disliked), payload.dislike_count);
    }
  });
}

async function sendReaction(button, phoneId, endpoint, payload) {
  if (button.classList.contains("is-busy")) return;
  button.classList.add("is-busy");
  try {
    const response = await fetch(`/api/phones/${phoneId}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitor_id: getVisitorId(), ...payload }),
    });
    if (!response.ok) throw new Error("reaction request failed");
    const result = await response.json();
    applyReactionPayload(phoneId, result);
    if (result.liked) showToast("已加入喜欢");
    else if (result.disliked) showToast("已标记不想看");
    else showToast("已取消反馈");
  } catch {
    showToast("操作失败，请稍后重试");
  } finally {
    button.classList.remove("is-busy");
  }
}

document.querySelectorAll("[data-like-phone]").forEach(button => {
  applyLikeState(button, likedPhones.has(String(button.dataset.likePhone)), button.querySelector(".like-count")?.textContent || 0);
  button.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    const phoneId = String(button.dataset.likePhone);
    const nextLiked = !likedPhones.has(phoneId);
    sendReaction(button, phoneId, "like", { liked: nextLiked });
  });
});

document.querySelectorAll("[data-dislike-phone]").forEach(button => {
  applyDislikeState(button, dislikedPhones.has(String(button.dataset.dislikePhone)), button.querySelector(".dislike-count")?.textContent || 0);
  button.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    const phoneId = String(button.dataset.dislikePhone);
    const nextDisliked = !dislikedPhones.has(phoneId);
    sendReaction(button, phoneId, "dislike", { disliked: nextDisliked });
  });
});

document.querySelectorAll("[data-min][data-max]").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelector("#min_budget").value = button.dataset.min;
    document.querySelector("#budget").value = button.dataset.max;
    document.querySelectorAll("[data-min][data-max]").forEach(item => item.classList.toggle("selected", item === button));
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
