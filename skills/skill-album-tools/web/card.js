function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function artNode(card, artRev) {
  const box = el("div", "card-art");
  if (card.hasArt) {
    const image = el("img");
    image.alt = "";
    image.draggable = false;
    image.src = `/api/cards/${encodeURIComponent(card.id)}/art?v=${artRev}`;
    image.addEventListener("error", () => {
      image.replaceWith(el("div", "card-empty", card.name));
    });
    box.append(image);
  } else {
    box.append(el("div", "card-empty", card.name));
  }
  box.append(el("div", "shine"), el("div", "glitter"));
  return box;
}

export function countText(card, usageState) {
  if (card.count !== null && card.count !== undefined) return `${card.count} 回`;
  return usageState === "scanning" ? "…" : "不明";
}

function isNew(card) {
  if (!card.firstSeen) return false;
  return Date.now() - Date.parse(card.firstSeen) < 7 * 86400000;
}

function frame(card) {
  const node = el("article", "card card-full");
  node.dataset.rarity = String(card.rarity);
  node.append(el("div", "shine"), el("div", "glitter"));
  return node;
}

function footer(card, ctx, full) {
  const foot = el("div", "card-foot");
  foot.append(el("span", "stars", "★".repeat(card.rarity)));
  if (!full) foot.append(el("span", "", countText(card, ctx.usageState)));
  const number = String(card.no).padStart(3, "0");
  foot.append(el("span", "", full ? `${number}/${String(ctx.maxNo).padStart(3, "0")}` : number));
  return foot;
}

function formatSize(bytes) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDate(seconds) {
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}

export function zoomFront(card, ctx, actions) {
  const node = frame(card);
  const head = el("div", "card-head");
  const cost = el("span", "card-cost");
  cost.dataset.tier = card.cost >= 8 ? "high" : card.cost >= 4 ? "mid" : "low";
  cost.append(el("span", "card-cost-value", card.cost));
  head.append(el("span", "card-kind", card.kind), el("span", "card-name", card.name), cost);
  const band = el("div", "band", `${ctx.folderName(card.folder)} / ${formatSize(card.size)} / 更新 ${formatDate(card.updated)}`);
  const text = el("div", "card-text");
  if (card.triggers.length) text.append(el("div", "triggers", `呼び出し語: ${card.triggers.join("、")}`));
  const command = el("div", "command");
  const use = el("button", "command-use", `/${card.id}`);
  use.type = "button";
  use.setAttribute("aria-label", `使う /${card.id}`);
  use.addEventListener("click", actions.use);
  command.append(use, el("b", "", countText(card, ctx.usageState)));
  if (ctx.startedAt) command.append(el("span", "", `${ctx.startedAt.slice(0, 10)} から`));
  text.append(command, el("div", "", card.summary));
  if (card.tools.length) {
    const tools = el("div", "tool-list");
    for (const tool of card.tools) tools.append(el("span", "", tool));
    text.append(tools);
  }
  node.append(head, artNode(card, ctx.artRev), band, text);
  if (card.flavor) node.append(el("div", "flavor", card.flavor));
  node.append(footer(card, ctx, true), el("div", "glare"));
  if (isNew(card)) node.append(el("span", "new", "NEW"));
  node.style.setProperty("--folder", ctx.color(card.folder));
  return node;
}

export function packBack() {
  const node = el("article", "card card-full card-back-face");
  node.dataset.rarity = "1";
  node.append(el("div", "back-pattern"));
  return node;
}

export function zoomBack(card, detail, ctx, actions) {
  const node = el("article", "card card-full card-back-face");
  node.dataset.rarity = "1";
  const body = el("div", "back-body");
  body.append(el("h3", "", card.name));
  const list = el("dl");
  const row = (label, value) => {
    if (!value) return;
    list.append(el("dt", "", label), el("dd", "", value));
  };
  row("引数", detail.argumentHint);
  row("最終使用", card.last ? card.last.slice(0, 10) : "");
  row("ファイル", detail.files.join("、"));
  row("パス", detail.path);
  if (card.parseError) row("状態", "先頭情報を読めません");
  const row2 = el("div", "back-actions");
  const button = (label, handler) => {
    const node = el("button", "", label);
    node.type = "button";
    node.addEventListener("click", handler);
    row2.append(node);
  };
  button("フォルダを開く", actions.openFolder);
  button("指示をコピー", actions.copyPrompt);
  if (card.hasPrev) button("元に戻す", actions.restore);
  if (card.hasArt && actions.isUnlocked) button("絵を削除", actions.deleteArt);
  body.append(list, row2);
  node.append(el("div", "back-pattern"), body);
  return node;
}
