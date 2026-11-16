import { zoomBack, zoomFront } from "./card.js";
import { attachHolo } from "./holo.js";
import { play, setSound } from "./sound.js";
import { showPack } from "./pack.js";

const CARDS_PER_PAGE = 4;
const ROWS_PER_PAGE = 2;
const CARD_RATIO = 1.4;
const GAP_PX = 12;
const HINGE_PX = 14;
const FLIP_MS = 700;
const BAR_EDGE_PX = 14;
const PRESS_SLOP_PX = 5;
const SINGLE_BELOW = 1200;
const LONG_PRESS_MS = 300;
const SOURCE_NAMES = { user: "自作", synced: "公式", plugins: "プラグイン" };
const MAX_IMAGE = 10 * 1024 * 1024;
const session = new URLSearchParams(location.search).get("s");
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let zoomScale = 1;
let closingAnimations = [];
let albumState = "open";
const tabNodes = new Map();
let foldFrame = 0;
let jumpTarget = null;
let closedSide = "front";
let isFoldBlocked = false;
let foldStart = null;
let activePack = null;
let cardZoom = 1;
let isSingle = false;
let book = null;
let isSyncingBook = false;
const bookSize = { width: 1, height: 1 };

const state = {
  cards: [],
  album: null,
  revision: 0,
  maxNo: 0,
  page: 0,
  usageState: "scanning",
  usageProgress: 0,
  startedAt: null,
  artRev: 0,
  textsRev: 0,
  server: null,
  message: "",
  notes: [],
  isOffline: false,
  isReadonly: false,
  zoomId: null,
  isBack: false,
  sections: [],
};

function api(method, path, body, headers) {
  const options = { method, headers: headers || {} };
  if (body !== undefined) {
    if (body instanceof Blob) {
      options.body = body;
    } else {
      options.body = JSON.stringify(body);
      options.headers["Content-Type"] = "application/json";
    }
  }
  return fetch(path, options);
}

async function apiJson(method, path, body) {
  const response = await api(method, path, body);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  return { status: response.status, data };
}

const cardPath = (id) => `/api/cards/${encodeURIComponent(id)}`;

function folders() {
  return [...state.album.folders].sort((a, b) => a.order - b.order);
}

const context = () => ({
  artRev: state.artRev,
  maxNo: state.maxNo,
  usageState: state.usageState,
  startedAt: state.startedAt,
  color: (id) => (state.album.folders.find((f) => f.id === id) || {}).color || "#D9CFD3",
  folderName: (id) => (state.album.folders.find((f) => f.id === id) || {}).name || "",
});

function visibleCards() {
  const query = $("search").value.trim().toLowerCase();
  const kind = $("kind").value;
  const art = $("art").value;
  const sort = $("sort").value;
  let rows = state.cards.filter((card) => {
    if (kind && card.kind !== kind) return false;
    if (art === "yes" && !card.hasArt) return false;
    if (art === "no" && card.hasArt) return false;
    if (query) {
      const text = [card.id, card.summary, card.flavor, card.triggers.join(" ")].join(" ").toLowerCase();
      return text.includes(query);
    }
    return true;
  });
  rows = [...rows];
  if (sort === "count") rows.sort((a, b) => (b.count || 0) - (a.count || 0) || a.no - b.no);
  else if (sort === "updated") rows.sort((a, b) => b.updated - a.updated || a.no - b.no);
  else rows.sort((a, b) => a.no - b.no);
  return rows;
}

function renderStatus() {
  const parts = [...state.notes];
  if (state.isOffline) parts.unshift("接続できません");
  if (state.usageState === "scanning") parts.push(`利用履歴を集計中 ${state.usageProgress}%`);
  if (state.usageState === "unreadable") parts.push("利用履歴を読めません");
  if (state.message) parts.unshift(state.message);
  $("status").textContent = parts.join(" / ");
  document.body.classList.toggle("offline", state.isOffline);
  document.body.classList.toggle("readonly", state.isReadonly);
}

function setMessage(text) {
  state.message = text;
  renderStatus();
}

function isSectioned() {
  return !$("search").value.trim() && $("sort").value === "no";
}

function jumpToSection(folderId) {
  const wasSectioned = isSectioned();
  if (!wasSectioned) {
    $("search").value = "";
    $("search").dispatchEvent(new Event("input", { bubbles: true }));
    $("sort").value = "no";
    render();
  }
  const section = state.sections.find((item) => item.id === folderId);
  const target = section?.count ? section.start : null;
  if (target === null || albumState !== "open" || $("binder").classList.contains("flipping")) return;
  const perView = isSingle ? 1 : 2;
  if (!wasSectioned) {
    state.page = target - (target % perView);
    render();
  } else if (Math.floor(target / perView) !== Math.floor(state.page / perView)) {
    jumpTarget = target;
    book.flip(target, "bottom");
  }
}

function renderTabs() {
  const list = folders();
  $("tabs-left").replaceChildren();
  $("tabs-right").replaceChildren();
  tabNodes.clear();
  list.forEach((folder, index) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "tab";
    tab.textContent = folder.name;
    tab.style.setProperty("--folder", folder.color);
    tab.style.gridRow = String(index + 1);
    tab.addEventListener("click", () => jumpToSection(folder.id));
    tab.dataset.folder = folder.id;
    $("tabs-right").append(tab);
    tabNodes.set(folder.id, tab);
  });
  updateTabSides(false);
}

function clearFoldTabs() {
  for (const tab of tabNodes.values()) {
    tab.style.transform = "";
    tab.style.opacity = "";
    tab.dataset.side = tab.parentElement === $("tabs-left") ? "left" : "right";
  }
}

function placeFoldTabs() {
  const calc = book.getFlipController().getCalculation();
  if (!calc || isSingle || !isSectioned()) {
    clearFoldTabs();
    return;
  }
  const isBack = calc.getDirection() === 1;
  const left = state.page - (state.page % 2);
  const target = jumpTarget === null ? left + (isBack ? -2 : 2) : jumpTarget - (jumpTarget % 2);
  const low = Math.min(left, target);
  const high = Math.max(left, target);
  const pageWidth = book.getRender().getRect().pageWidth;
  const progress = Math.min(1, Math.max(0, (pageWidth - calc.getPosition().x) / (2 * pageWidth)));
  const bookWidth = $("book").offsetWidth;
  for (const section of state.sections) {
    const tab = tabNodes.get(section.id);
    if (!tab) continue;
    const isMoving = section.start > low && section.start <= high;
    const distance = (bookWidth + tab.offsetWidth) * (isBack ? 1 : -1);
    const isOver = progress >= 0.5;
    tab.style.transform = isMoving && isOver ? `translateX(${distance}px)` : "";
    tab.style.opacity = isMoving ? String(Math.abs(progress - 0.5) * 2) : "";
    const isOnLeft = isBack ? !(isMoving && isOver) : isMoving && isOver;
    tab.dataset.side = isMoving ? (isOnLeft ? "left" : "right") : tab.parentElement === $("tabs-left") ? "left" : "right";
  }
}

function followFold() {
  if (foldFrame) return;
  const step = () => {
    if (book.getFlipController().getCalculation() || $("binder").classList.contains("flipping")) {
      placeFoldTabs();
      foldFrame = requestAnimationFrame(step);
      return;
    }
    foldFrame = 0;
    jumpTarget = null;
    clearFoldTabs();
  };
  foldFrame = requestAnimationFrame(step);
}

function updateTabSides(isAnimated) {
  const perView = isSingle ? 1 : 2;
  const left = state.page - (state.page % perView);
  const right = left + perView - 1;
  const hasSections = isSectioned();
  let current = null;
  const starts = new Map();
  for (const section of state.sections) {
    starts.set(section.id, section.start);
    if (hasSections && section.count && section.start <= right) current = section.id;
    tabNodes.get(section.id)?.classList.toggle("is-empty", !section.count);
  }
  for (const [id, tab] of tabNodes) {
    const isPassed = isSingle || (hasSections && (starts.get(id) ?? Infinity) <= left);
    const host = isPassed ? $("tabs-left") : $("tabs-right");
    tab.setAttribute("aria-current", String(id === current));
    tab.dataset.side = isPassed ? "left" : "right";
    if (tab.parentElement === host) continue;
    const from = tab.getBoundingClientRect();
    tab.style.transform = "";
    tab.style.opacity = "";
    host.append(tab);
    if (!isAnimated || !from.width || matchMedia("(prefers-reduced-motion: reduce)").matches) continue;
    const to = tab.getBoundingClientRect();
    tab.animate([{ transform: `translateX(${from.left - to.left}px)` }, { transform: "none" }], { duration: 300, easing: "cubic-bezier(.2,.8,.2,1)" });
  }
}

function fillSheet(rows) {
  const sheet = document.createElement("div");
  sheet.className = "leaf";
  const page = document.createElement("section");
  page.className = "page";
  const ctx = context();
  for (let index = 0; index < CARDS_PER_PAGE; index += 1) {
    const pocket = document.createElement("div");
    pocket.className = "pocket";
    const card = rows[index];
    if (card) {
      const node = zoomFront(card, ctx, { use: () => {} });
      node.querySelector(".command-use").tabIndex = -1;
      node.dataset.cardId = card.id;
      node.setAttribute("tabindex", "0");
      node.setAttribute("role", "button");
      node.setAttribute("aria-label", card.name);
      node.addEventListener("click", () => openZoom(card.id, node));
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openZoom(card.id, node);
        }
      });
      attachHolo(node, pocket, 6);
      pocket.append(node);
    } else {
      pocket.classList.add("empty");
    }
    page.append(pocket);
  }
  sheet.append(page);
  return sheet;
}

function buildSheets() {
  const rows = visibleCards();
  const sheets = [];
  const addChunks = (items) => {
    for (let index = 0; index < items.length; index += CARDS_PER_PAGE) {
      sheets.push(fillSheet(items.slice(index, index + CARDS_PER_PAGE)));
    }
  };
  state.sections = [];
  if (isSectioned()) {
    const list = folders();
    const known = new Set(list.map((folder) => folder.id));
    for (const folder of list) {
      const items = rows.filter((card) => (known.has(card.folder) ? card.folder : "uncategorized") === folder.id);
      if (items.length && !isSingle && sheets.length % 2) sheets.push(fillSheet([]));
      state.sections.push({ id: folder.id, start: sheets.length, count: items.length });
      addChunks(items);
    }
  } else {
    addChunks(rows);
  }
  const minimum = isSingle ? 1 : 2;
  while (sheets.length < minimum || (!isSingle && sheets.length % 2)) sheets.push(fillSheet([]));
  return sheets;
}

function updatePager(isAnimated = false) {
  const pages = book ? book.getPageCount() : 1;
  const perView = isSingle ? 1 : 2;
  const views = Math.ceil(pages / perView);
  const current = Math.floor(state.page / perView);
  $("pager").textContent = views > 1 ? `${current + 1} / ${views}` : "";
  $("prev").hidden = current === 0;
  $("next").hidden = current >= views - 1;
  updateTabSides(isAnimated);
}

function onBookState(next) {
  $("binder").classList.toggle("flipping", next !== "read");
  if (next === "flipping") play("flip");
  if (next !== "read") followFold();
}

function pointInBook(x, y) {
  const box = document.querySelector("#book .stf__block")?.getBoundingClientRect();
  return box ? { x: x - box.left, y: y - box.top } : null;
}

function lockFoldDirection(controller) {
  const fold = controller.fold.bind(controller);
  controller.fold = (point) => {
    if (controller.getCalculation() === null) {
      if (isFoldBlocked || !controller.start(foldStart ?? point)) {
        isFoldBlocked = true;
        return;
      }
    }
    fold(point);
  };
}

function renderBook() {
  const sheets = buildSheets();
  state.page = Math.min(state.page, sheets.length - 1);
  isSyncingBook = true;
  if (!book) {
    const isReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    book = new window.St.PageFlip($("book"), {
      width: bookSize.width,
      height: bookSize.height,
      size: "fixed",
      autoSize: false,
      usePortrait: true,
      showCover: false,
      showPageCorners: false,
      disableFlipByClick: false,
      mobileScrollSupport: false,
      flippingTime: isReduced ? 1 : FLIP_MS,
      drawShadow: !isReduced,
      maxShadowOpacity: 0.35,
      startPage: state.page,
    });
    book.on("flip", (event) => {
      if (isSyncingBook) return;
      state.page = event.data;
      updatePager(true);
    });
    book.on("changeState", (event) => onBookState(event.data));
    book.loadFromHTML(sheets);
    lockFoldDirection(book.getFlipController());
    $("book").style.minWidth = "0";
    $("book").style.minHeight = "0";
  } else {
    book.getRender().finishAnimation();
    book.getFlipController().reset();
    book.getRender().setBottomPage(null);
    book.getRender().setFlippingPage(null);
    book.getRender().clearShadow();
    book.updateFromHtml(sheets);
    book.turnToPage(state.page);
  }
  isSyncingBook = false;
  updatePager();
}

function render() {
  renderTabs();
  renderStatus();
  layout();
  renderBook();
}

function syncSingle() {
  const next = window.innerWidth < SINGLE_BELOW;
  if (next === isSingle) return false;
  isSingle = next;
  document.body.classList.toggle("single", isSingle);
  if (state.album) renderTabs();
  return true;
}

function layout() {
  const isChanged = syncSingle();
  const tabsHeight = isSingle ? $("tabs-left").offsetHeight : 0;
  const root = document.documentElement.style;
  root.setProperty("--tabs-h", `${tabsHeight}px`);
  const height = window.innerHeight - tabsHeight - 52;
  const rowsHeight = ROWS_PER_PAGE * CARD_RATIO;
  const gapsHeight = (ROWS_PER_PAGE + 1) * GAP_PX;
  const byHeight = (height - gapsHeight) / rowsHeight;
  const byWidth = isSingle ? (window.innerWidth - 124) / 2 : (window.innerWidth - 300 - 100) / 4;
  const cardWidth = Math.floor(Math.max(isSingle ? 90 : 120, Math.min(byHeight, byWidth, 300)));
  root.setProperty("--cw", `${cardWidth}px`);
  bookSize.width = 2 * cardWidth + 36 + (isSingle ? 0 : HINGE_PX);
  bookSize.height = Math.ceil(rowsHeight * cardWidth + gapsHeight);
  root.setProperty("--book-w", `${bookSize.width * (isSingle ? 1 : 2)}px`);
  root.setProperty("--book-h", `${bookSize.height}px`);
  cardZoom = cardWidth / 390;
  root.setProperty("--card-zoom", cardZoom.toFixed(4));
  zoomScale = Math.min(1.35, (window.innerHeight - 40) / 546, (window.innerWidth - 40) / 390);
  $("zoom-stage").style.setProperty("--zs", zoomScale.toFixed(3));
  if (albumState === "closed") $("cover").style.transform = coverClosedTransform(placeCover());
  if (!book) return;
  const settings = book.getSettings();
  settings.width = bookSize.width;
  settings.height = bookSize.height;
  isSyncingBook = true;
  book.update();
  isSyncingBook = false;
  if (isChanged) renderBook();
}

function setAlbumLocked(isLocked) {
  document.querySelector(".bar").inert = isLocked;
  document.querySelector(".stage").inert = isLocked;
  $("cover").inert = isLocked;
}

function placeCover() {
  const rect = $("binder").getBoundingClientRect();
  const cover = $("cover");
  cover.style.left = `${rect.left}px`;
  cover.style.top = `${rect.top}px`;
  cover.style.width = `${isSingle ? rect.width : rect.width / 2}px`;
  cover.style.height = `${rect.height}px`;
  return rect;
}

function coverClosedTransform(rect) {
  if (isSingle) return "none";
  return closedSide === "back" ? `translateX(${rect.width / 4}px)` : `translateX(${-rect.width / 4}px) rotateY(180deg)`;
}

function albumLeaf(rect, side) {
  const leaf = document.createElement("div");
  leaf.className = `cover-face cover-inner flip-leaf flip-${side}`;
  for (const corner of side === "left" ? ["tl", "bl"] : ["tr", "br"]) {
    const mark = document.createElement("span");
    mark.className = `corner corner-${corner}`;
    leaf.append(mark);
  }
  const source = [...document.querySelectorAll(`#book .stf__item.--${side} .page`)].find((node) => node.getBoundingClientRect().width);
  if (source) {
    const box = source.getBoundingClientRect();
    const clone = source.cloneNode(true);
    const originLeft = side === "right" ? rect.left + rect.width / 2 : rect.left;
    clone.style.position = "absolute";
    clone.style.left = `${box.left - (isSingle ? rect.left : originLeft)}px`;
    clone.style.top = `${box.top - rect.top}px`;
    clone.style.width = `${box.width}px`;
    clone.style.height = `${box.height}px`;
    clone.style.margin = "0";
    leaf.append(clone);
  }
  return leaf;
}

function albumOuterLeaf(isBack = false) {
  const leaf = $("cover").querySelector(".cover-outer").cloneNode(true);
  leaf.classList.add("flip-leaf");
  leaf.classList.toggle("flip-back", isBack);
  return leaf;
}

function waitForFlip(flip) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, FLIP_MS + 500);
    flip.on("changeState", (event) => {
      if (event.data !== "read") return;
      clearTimeout(timer);
      resolve();
    });
  });
}

function buildAlbumFlip(rect, isClosing, duration, side = "front") {
  const isReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const host = $("album-flip");
  const pageWidth = isSingle ? rect.width : rect.width / 2;
  host.style.left = `${rect.left}px`;
  host.style.top = `${rect.top}px`;
  host.style.width = `${rect.width}px`;
  host.style.height = `${rect.height}px`;
  const target = document.createElement("div");
  target.style.width = `${rect.width}px`;
  target.style.height = `${rect.height}px`;
  host.replaceChildren(target);
  host.hidden = false;
  const leaves = isSingle
    ? [isClosing ? albumLeaf(rect, "right") : albumOuterLeaf(), isClosing ? albumOuterLeaf() : albumLeaf(rect, "right"), albumOuterLeaf()]
    : [albumOuterLeaf(), albumLeaf(rect, "left"), albumLeaf(rect, "right"), albumOuterLeaf(true)];
  const flip = new window.St.PageFlip(target, {
    width: pageWidth,
    height: rect.height,
    size: "fixed",
    autoSize: false,
    usePortrait: isSingle,
    showCover: !isSingle,
    showPageCorners: false,
    disableFlipByClick: true,
    useMouseEvents: false,
    mobileScrollSupport: false,
    flippingTime: duration,
    drawShadow: !isReduced,
    maxShadowOpacity: 0.35,
    startPage: isSingle ? 0 : isClosing ? 1 : side === "back" ? 3 : 0,
  });
  flip.loadFromHTML(leaves);
  target.style.minWidth = "0";
  target.style.minHeight = "0";
  return flip;
}

function clearAlbumFlip(flip, slide) {
  slide?.cancel();
  flip.destroy();
  $("album-flip").replaceChildren();
  $("album-flip").hidden = true;
}

async function flipAlbum(rect, isClosing) {
  const isReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const duration = isReduced ? 1 : FLIP_MS;
  const host = $("album-flip");
  const side = isClosing ? "front" : closedSide;
  const flip = buildAlbumFlip(rect, isClosing, duration, side);
  await sleep(30);
  document.body.classList.remove("album-closed");
  document.body.classList.add("album-moving");
  $("cover").hidden = true;
  const shift = isSingle ? 0 : ((side === "back" ? 1 : -1) * rect.width) / 4;
  const frames = [{ transform: "translateX(0)" }, { transform: `translateX(${shift}px)` }];
  const slide = host.animate(isClosing ? frames : [...frames].reverse(), { duration, easing: "ease-in-out", fill: "forwards" });
  const finished = waitForFlip(flip);
  if (!isSingle && (isClosing || side === "back")) flip.flipPrev("top");
  else flip.flipNext("top");
  await finished;
  return () => clearAlbumFlip(flip, slide);
}

function showClosedAlbum(rect, clear, side = "front") {
  closedSide = side;
  $("cover").classList.toggle("from-back", side === "back");
  document.body.classList.replace("album-moving", "album-closed");
  $("cover").style.transform = coverClosedTransform(rect);
  $("cover").hidden = false;
  clear();
  albumState = "closed";
  setAlbumLocked(false);
  $("cover").focus({ preventScroll: true });
}

function startAlbumFold(startX, startY, side) {
  albumState = "moving";
  setAlbumLocked(true);
  const rect = placeCover();
  const duration = matchMedia("(prefers-reduced-motion: reduce)").matches ? 1 : FLIP_MS;
  const flip = buildAlbumFlip(rect, true, duration, side);
  document.body.classList.add("album-moving");
  const controller = flip.getFlipController();
  const box = document.querySelector("#album-flip .stf__block").getBoundingClientRect();
  const foldAt = (x, y) => controller.fold({ x: x - box.left, y: y - box.top });
  foldAt(startX, startY);
  return {
    move: foldAt,
    async release() {
      const finished = waitForFlip(flip);
      if ((controller.getCalculation()?.getPosition().x ?? 1) <= 0) play("flip");
      controller.stopMove();
      await finished;
      if (flip.getCurrentPageIndex() === (side === "back" ? 3 : 0)) {
        const shift = ((side === "back" ? 1 : -1) * rect.width) / 4;
        const slide = $("album-flip").animate([{ transform: "translateX(0)" }, { transform: `translateX(${shift}px)` }], { duration: duration / 3, easing: "ease-out", fill: "forwards" });
        await slide.finished;
        showClosedAlbum(rect, () => clearAlbumFlip(flip, slide), side);
        return;
      }
      document.body.classList.remove("album-moving");
      clearAlbumFlip(flip);
      albumState = "open";
      setAlbumLocked(false);
    },
  };
}

function startCoverFold(startX, startY) {
  albumState = "moving";
  setAlbumLocked(true);
  const rect = placeCover();
  const duration = matchMedia("(prefers-reduced-motion: reduce)").matches ? 1 : FLIP_MS;
  const side = closedSide;
  const flip = buildAlbumFlip(rect, false, duration, side);
  const host = $("album-flip");
  const shift = isSingle ? 0 : ((side === "back" ? 1 : -1) * rect.width) / 4;
  host.style.transform = `translateX(${shift}px)`;
  document.body.classList.replace("album-closed", "album-moving");
  $("cover").hidden = true;
  const controller = flip.getFlipController();
  const box = document.querySelector("#album-flip .stf__block").getBoundingClientRect();
  const foldAt = (x, y) => controller.fold({ x: x - box.left, y: y - box.top });
  const clear = () => {
    host.style.transform = "";
    clearAlbumFlip(flip);
  };
  foldAt(isSingle ? box.right - 8 : startX, startY);
  return {
    move: foldAt,
    async release() {
      const finished = waitForFlip(flip);
      if ((controller.getCalculation()?.getPosition().x ?? 1) <= 0) play("flip");
      controller.stopMove();
      await finished;
      const index = flip.getCurrentPageIndex();
      if (side === "back" ? index < 3 : index > 0) {
        await host.animate([{ transform: `translateX(${shift}px)` }, { transform: "translateX(0)" }], { duration: duration / 3, easing: "ease-out", fill: "forwards" }).finished;
        document.body.classList.remove("album-moving");
        host.getAnimations().forEach((animation) => animation.cancel());
        clear();
        albumState = "open";
        setAlbumLocked(false);
        maybeShowPack();
        return;
      }
      showClosedAlbum(rect, clear, side);
    },
  };
}

function bindBar() {
  const bar = document.querySelector(".bar");
  const syncPin = () => bar.classList.toggle("is-pinned", Boolean($("search").value.trim() || $("kind").value || $("art").value));
  const onPointer = (event) => {
    if (event.buttons) return;
    if (event.clientY <= BAR_EDGE_PX) {
      bar.classList.add("is-open");
    } else if (event.clientY > bar.offsetHeight + BAR_EDGE_PX && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
      bar.classList.remove("is-open");
    }
  };
  window.addEventListener("pointermove", onPointer);
  window.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.clientY > bar.offsetHeight) onPointer({ buttons: 0, clientY: event.clientY });
  });
  for (const id of ["search", "kind", "art"]) {
    $(id).addEventListener("input", syncPin);
    $(id).addEventListener("change", syncPin);
  }
}

function bindCoverFold() {
  const cover = $("cover");
  let press = null;
  let fold = null;
  let wasDragged = false;
  cover.addEventListener("mousedown", (event) => {
    if (event.button !== 0 || albumState !== "closed") return;
    wasDragged = false;
    press = { x: event.clientX, y: event.clientY };
  });
  window.addEventListener("mousemove", (event) => {
    if (fold) {
      fold.move(event.clientX, event.clientY);
    } else if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > PRESS_SLOP_PX) {
      wasDragged = true;
      fold = startCoverFold(press.x, press.y);
      fold.move(event.clientX, event.clientY);
    }
  });
  window.addEventListener("mouseup", () => {
    press = null;
    fold?.release();
    fold = null;
  });
  cover.addEventListener("click", () => {
    if (!wasDragged) openAlbum();
    wasDragged = false;
  });
}

async function closeAlbum() {
  if (albumState !== "open" || $("binder").classList.contains("flipping")) return;
  albumState = "moving";
  setAlbumLocked(true);
  const rect = placeCover();
  play("flip");
  showClosedAlbum(rect, await flipAlbum(rect, true));
}

async function openAlbum() {
  if (albumState !== "closed") return;
  albumState = "moving";
  setAlbumLocked(true);
  const rect = placeCover();
  play("flip");
  const clear = await flipAlbum(rect, false);
  document.body.classList.remove("album-moving");
  clear();
  albumState = "open";
  setAlbumLocked(false);
  maybeShowPack();
}

async function maybeShowPack() {
  if (activePack || albumState !== "open" || state.zoomId) return;
  const cards = state.cards.filter((card) => card.packed).sort((a, b) => a.no - b.no);
  if (!cards.length) return;
  activePack = showPack({
    cards,
    ctx: context(),
    play,
    setLocked: setAlbumLocked,
    markOpened: async (ids) => {
      try {
        await apiJson("POST", "/api/pack/open", { ids });
        for (const card of cards) card.packed = false;
      } catch {
        setMessage("保存できませんでした");
      }
    },
    targetFor: (id) => {
      const folder = cards.find((card) => card.id === id)?.folder;
      const tab = [...document.querySelectorAll(".tab[data-folder]")].find((node) => node.dataset.folder === folder);
      return tab && tab.getBoundingClientRect().width ? tab : $("binder");
    },
  });
  await activePack.finished;
  activePack = null;
}

function turn(delta) {
  if (!book) return;
  if (delta > 0) book.flipNext("bottom");
  else book.flipPrev("bottom");
}

function bindBook() {
  const host = $("book");
  let press = null;
  let drag = null;
  let albumFold = null;
  let isClickSuppressed = false;

  const suppressClick = () => {
    isClickSuppressed = true;
    setTimeout(() => {
      isClickSuppressed = false;
    }, 0);
  };
  const tabAt = (x, y) => {
    const tab = document.elementFromPoint(x, y)?.closest(".tab");
    return tab?.dataset.folder ? tab : null;
  };
  const moveGhost = (x, y) => {
    drag.ghost.style.setProperty("--gx", `${x / cardZoom - 195}px`);
    drag.ghost.style.setProperty("--gy", `${y / cardZoom - 273}px`);
    const tab = tabAt(x, y);
    if (tab !== drag.tab) {
      drag.tab?.classList.remove("drop-target");
      tab?.classList.add("drop-target");
      drag.tab = tab;
    }
  };
  const startCardDrag = () => {
    book.isUserTouch = false;
    const ghost = press.card.cloneNode(true);
    ghost.classList.add("card-ghost");
    document.body.append(ghost);
    press.card.classList.add("card-lifted");
    drag = { ghost, card: press.card, tab: null };
    moveGhost(press.x, press.y);
    press = null;
  };

  host.addEventListener(
    "touchstart",
    (event) => {
      isFoldBlocked = false;
      const touch = event.touches[0];
      foldStart = touch ? pointInBook(touch.clientX, touch.clientY) : null;
    },
    { capture: true, passive: true },
  );
  host.addEventListener(
    "mousedown",
    (event) => {
      isFoldBlocked = false;
      foldStart = pointInBook(event.clientX, event.clientY);
      if (event.button !== 0 || event.target.closest("button, a")) return;
      const card = event.target.closest(".card");
      press = { x: event.clientX, y: event.clientY, card, isMoved: false, timer: null };
      if (card && !state.isReadonly && !state.isOffline) press.timer = setTimeout(startCardDrag, LONG_PRESS_MS);
    },
    true,
  );
  $("binder").addEventListener(
    "mousedown",
    (event) => {
      if (event.button !== 0 || host.contains(event.target) || event.target.closest(".tab, button:not(.turn), a")) return;
      const box = host.getBoundingClientRect();
      const edgeX = Math.min(Math.max(event.clientX, box.left + 1), box.right - 1);
      const edgeY = Math.min(Math.max(event.clientY, box.top + 1), box.bottom - 1);
      isFoldBlocked = false;
      foldStart = pointInBook(edgeX, edgeY);
      press = { x: event.clientX, y: event.clientY, card: null, isEdge: true, isMoved: false, timer: null };
      book.startUserTouch(foldStart);
    },
    true,
  );
  window.addEventListener(
    "mousemove",
    (event) => {
      if (drag) {
        moveGhost(event.clientX, event.clientY);
        return;
      }
      if (albumFold) {
        albumFold.move(event.clientX, event.clientY);
        return;
      }
      if (press && !press.isMoved && Math.hypot(event.clientX - press.x, event.clientY - press.y) > PRESS_SLOP_PX) {
        press.isMoved = true;
        clearTimeout(press.timer);
      }
      if (press?.isMoved && !isSingle && albumState === "open") {
        const box = host.getBoundingClientRect();
        const isOnLeft = press.x < box.left + box.width / 2;
        const moved = event.clientX - press.x;
        const side = isOnLeft && $("prev").hidden && moved > PRESS_SLOP_PX ? "front" : !isOnLeft && $("next").hidden && moved < -PRESS_SLOP_PX ? "back" : null;
        if (side) {
          book.isUserTouch = false;
          albumFold = startAlbumFold(press.x, press.y, side);
          albumFold.move(event.clientX, event.clientY);
        }
      }
    },
    true,
  );
  window.addEventListener(
    "mouseup",
    () => {
      if (albumFold) {
        albumFold.release();
        albumFold = null;
        press = null;
        suppressClick();
        return;
      }
      if (drag) {
        const { ghost, card, tab } = drag;
        drag = null;
        ghost.remove();
        card.classList.remove("card-lifted");
        tab?.classList.remove("drop-target");
        suppressClick();
        if (tab) moveCard(card.dataset.cardId, tab.dataset.folder);
        return;
      }
      if (!press) return;
      clearTimeout(press.timer);
      if (press.isMoved) {
        suppressClick();
        if (book.getFlipController().getCalculation()?.getPosition().x <= 0) play("flip");
      } else if (press.card || press.isEdge) {
        book.isUserTouch = false;
      }
      press = null;
      setTimeout(() => {
        if (book.getFlipController().getCalculation() === null) book.getFlipController().setState("read");
      }, 0);
    },
    true,
  );
  document.addEventListener(
    "click",
    (event) => {
      if (isClickSuppressed) {
        event.stopPropagation();
        event.preventDefault();
      }
    },
    true,
  );
}

async function moveCard(id, folder) {
  const card = state.cards.find((c) => c.id === id);
  if (!card || card.folder === folder) return;
  const previous = card.folder;
  card.folder = folder;
  render();
  const ok = await saveAlbum({ cards: { [id]: { folder } } });
  if (!ok) {
    card.folder = previous;
    render();
  }
}

async function saveAlbum(change) {
  let result;
  try {
    result = await apiJson("PUT", "/api/album", { revision: state.revision, ...change });
  } catch {
    setMessage("保存できませんでした");
    return false;
  }
  if (result.status === 200) {
    state.album = result.data;
    state.revision = result.data.revision;
    setMessage("");
    return true;
  }
  if (result.data.reason === "revision") {
    await reload();
    setMessage("別の画面で変更されたので読み直しました");
  } else if (result.data.reason === "readonly") {
    setMessage("新しい版のデータなので読み取り専用です");
  } else if (result.data.reason === "folders-limit") {
    setMessage("フォルダは 16 個まで");
  } else {
    setMessage("保存できませんでした");
  }
  return false;
}

async function reload() {
  const [cards, album, usage] = await Promise.all([
    apiJson("GET", "/api/cards"),
    apiJson("GET", "/api/album"),
    apiJson("GET", "/api/usage"),
  ]);
  state.cards = cards.data.cards;
  state.maxNo = cards.data.maxNo;
  state.album = album.data;
  state.revision = album.data.revision;
  state.usageState = usage.data.state;
  state.usageProgress = usage.data.progress;
  state.startedAt = usage.data.startedAt;
  state.isReadonly = cards.data.readonly;
  const notes = [];
  for (const source of cards.data.lost) notes.push(`${SOURCE_NAMES[source] || source} 0 件`);
  const unreadable = ["plugin-unresolved", "invalid-name", "card-error", "source-error"].reduce((sum, kind) => sum + (cards.data.diagnostics[kind] || 0), 0);
  if (unreadable) notes.push(`読めない Skill が ${unreadable} 件`);
  if (cards.data.notice === "restored") notes.push("分類を予備から復元しました");
  if (cards.data.notice === "reset") notes.push("分類を読めないので初期状態で開きました");
  if (cards.data.readonly) notes.push("新しい版のデータなので読み取り専用です");
  state.notes = notes;
  setSound(state.album.sound);
  $("sound").setAttribute("aria-pressed", String(state.album.sound));
  render();
  if (state.zoomId) refreshZoom();
}

async function poll() {
  try {
    const { data } = await apiJson("GET", "/api/state");
    if (state.server && data.version !== state.server) {
      location.reload();
      return;
    }
    state.server = data.version;
    const changed =
      state.isOffline ||
      data.albumRevision !== state.revision ||
      data.cardsRev !== state.cardsRev ||
      data.textsRev !== state.textsRev ||
      data.artRev !== state.artRev ||
      data.usage.state !== state.usageState;
    state.isOffline = false;
    state.usageProgress = data.usage.progress;
    state.cardsRev = data.cardsRev;
    state.textsRev = data.textsRev;
    state.artRev = data.artRev;
    if (changed) await reload();
    else renderStatus();
  } catch {
    state.isOffline = true;
    renderStatus();
  }
}

// ---- 拡大表示
const zoomCard = () => state.cards.find((c) => c.id === state.zoomId);
let detachZoomHolo = () => {};
let slideTimer = null;
let isFlipping = false;

function setUnlocked(value) {
  const stage = $("zoom-stage");
  if (stage.classList.contains("unlocked") !== value) play("slide");
  stage.classList.add("sliding");
  clearTimeout(slideTimer);
  slideTimer = setTimeout(() => stage.classList.remove("sliding"), 340);
  stage.classList.toggle("unlocked", value);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const focused = document.activeElement;
    const buffer = document.createElement("textarea");
    buffer.className = "copy-buffer";
    buffer.value = text;
    document.body.append(buffer);
    buffer.select();
    const isCopied = document.execCommand("copy");
    buffer.remove();
    focused?.focus();
    return isCopied;
  }
}

async function copyArtPrompt() {
  const { data } = await apiJson("GET", `${cardPath(state.zoomId)}/prompt`);
  if (data.state !== "ok") {
    $("art-result").textContent = "図柄説明なし";
    return;
  }
  $("art-result").textContent = (await copyText(data.text)) ? "コピーしました" : "コピーできませんでした";
}

async function restoreArt() {
  await apiJson("POST", `${cardPath(state.zoomId)}/art/restore`);
  await reload();
}

async function deleteArt() {
  await apiJson("DELETE", `${cardPath(state.zoomId)}/art`);
  await reload();
}

async function renderZoomFace() {
  const card = zoomCard();
  if (!card) return closeZoom();
  const stage = $("zoom-stage");
  const previous = stage.querySelector(".card");
  let node;
  if (state.isBack) {
    const { data } = await apiJson("GET", cardPath(card.id));
    if (state.zoomId !== card.id) return undefined;
    node = zoomBack(card, data, context(), {
      openFolder: () => apiJson("POST", `${cardPath(card.id)}/open`),
      copyPrompt: copyArtPrompt,
      restore: restoreArt,
      deleteArt,
      isUnlocked: stage.classList.contains("unlocked"),
    });
  } else {
    node = zoomFront(card, context(), { use: useCard });
  }
  detachZoomHolo();
  detachZoomHolo = attachHolo(node, stage, 18, stage);
  if (previous) previous.replaceWith(node);
  else stage.prepend(node);
  return node;
}

function refreshZoom() {
  renderZoomFace();
}

async function openZoom(id, origin) {
  endZoomClose();
  state.zoomId = id;
  state.isBack = false;
  const stage = $("zoom-stage");
  stage.replaceChildren();
  stage.classList.remove("unlocked", "showing-back");
  $("use-result").textContent = "";
  $("art-result").textContent = "";
  $("zoom").hidden = false;
  const node = await renderZoomFace();
  const sleeve = document.createElement("div");
  sleeve.className = "sleeve";
  stage.append(sleeve);
  play("click");
  if (origin && node) flyZoom(origin, "normal");
  stage.querySelector(".command-use")?.focus();
}

function flyZoom(origin, direction) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
  const stage = $("zoom-stage");
  const from = origin.getBoundingClientRect();
  if (!from.width) return null;
  const to = stage.getBoundingClientRect();
  return stage.animate(
    [
      { transform: `translate(${(from.left + from.width / 2 - to.left - to.width / 2) / zoomScale}px, ${(from.top + from.height / 2 - to.top - to.height / 2) / zoomScale}px) scale(${from.width / to.width})` },
      { transform: "none" },
    ],
    direction === "reverse"
      ? { duration: 260, easing: "cubic-bezier(.8,0,.6,1)", direction, fill: "forwards" }
      : { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" },
  );
}

function endZoomClose() {
  closingAnimations.forEach((animation) => animation.cancel());
  closingAnimations = [];
}

function closeZoom() {
  if (!state.zoomId) return;
  play("click");
  const origin = [...document.querySelectorAll("[data-card-id]")].find(
    (node) => node.dataset.cardId === state.zoomId && node.getBoundingClientRect().width,
  );
  state.zoomId = null;
  detachZoomHolo();
  detachZoomHolo = () => {};
  const finish = () => {
    endZoomClose();
    $("zoom").hidden = true;
    $("zoom-stage").replaceChildren();
  };
  const flight = origin && flyZoom(origin, "reverse");
  if (!flight) {
    finish();
    return;
  }
  closingAnimations = [
    flight,
    $("zoom").animate({ backgroundColor: "transparent", backdropFilter: "blur(0)" }, { duration: 260, fill: "forwards" }),
  ];
  flight.finished.then(finish, () => {});
}

function isOwnControl(event) {
  const { target } = event;
  if (target.closest("input, button, a")) return true;
  const text = target.closest(".card-text");
  return Boolean(text && text.scrollHeight > text.clientHeight && event.offsetX >= text.clientWidth);
}

async function flipCard() {
  if (isFlipping || !state.zoomId) return;
  isFlipping = true;
  play("turn");
  const stage = $("zoom-stage");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const parts = () => {
    const list = [stage.querySelector(".card")];
    if (!stage.classList.contains("unlocked")) list.push(stage.querySelector(".sleeve"));
    return list;
  };
  const turn = (from, to) =>
    Promise.all(
      parts().map(
        (part) =>
          part.animate([{ transform: `rotateY(${from}deg)` }, { transform: `rotateY(${to}deg)` }], {
            duration: 150,
            easing: "ease-in-out",
            composite: "add",
          }).finished,
      ),
    );
  try {
    if (!reduced) await turn(0, 90);
    state.isBack = !state.isBack;
    stage.classList.toggle("showing-back", state.isBack);
    await renderZoomFace();
    if (!reduced) await turn(-90, 0);
  } finally {
    isFlipping = false;
  }
}

function bindZoomStage() {
  const stage = $("zoom-stage");
  let start = null;
  stage.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || isOwnControl(event)) return;
    start = { x: event.clientX, y: event.clientY };
    if (!state.isBack) stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointerup", (event) => {
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    start = null;
    if (!state.isBack && Math.abs(dy) > 40) {
      const isWantedOut = dy < 0;
      if (isWantedOut !== stage.classList.contains("unlocked")) {
        setUnlocked(isWantedOut);
        return;
      }
    }
    if (Math.hypot(dx, dy) > 6) return;
    flipCard();
  });
  stage.addEventListener("pointercancel", () => {
    start = null;
  });
}

async function useCard() {
  const card = zoomCard();
  if (!card) return;
  const command = `/${card.id}`;
  const copied = await copyText(command);
  let filled = false;
  if (session) {
    try {
      const { status, data } = await apiJson("POST", `${cardPath(card.id)}/use?s=${encodeURIComponent(session)}`);
      if (status === 200) filled = await waitFilled(data.seq);
    } catch {
      filled = false;
    }
  }
  if (filled) $("use-result").textContent = "入力しました";
  else if (copied) $("use-result").textContent = "コピーしました";
  else $("use-result").textContent = "コピーできませんでした";
}

async function waitFilled(seq) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    const { data } = await apiJson("GET", `/api/use-result?s=${encodeURIComponent(session)}&seq=${seq}`);
    if (data.state === "filled") return true;
    if (data.state === "refused") return false;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

// ---- 画像
async function putImage(id, file, ifAbsent) {
  if (file.size > MAX_IMAGE) return "10MB まで";
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) return "PNG、JPEG、WebP のみ";
  let response;
  try {
    response = await api("PUT", `${cardPath(id)}/art${ifAbsent ? "?ifAbsent=1" : ""}`, file, { "Content-Type": file.type });
  } catch {
    return "保存できませんでした";
  }
  if (response.ok) return null;
  const data = await response.json().catch(() => ({}));
  if (data.reason === "exists") return "exists";
  if (data.reason === "too-large") return "10MB まで";
  if (data.reason === "format") return "PNG、JPEG、WebP のみ";
  if (data.reason === "readonly") return "新しい版のデータなので読み取り専用です";
  return "保存できませんでした";
}

async function bulkRegister(files) {
  const byNo = new Map(state.cards.map((card) => [card.no, card]));
  let added = 0;
  let skipped = 0;
  const unmatched = [];
  const failed = [];
  for (const file of files) {
    const match = /^(\d{3})_/.exec(file.name);
    const card = match ? byNo.get(Number(match[1])) : null;
    if (!card) {
      unmatched.push(file.name);
      continue;
    }
    const problem = await putImage(card.id, file, true);
    if (problem === null) added += 1;
    else if (problem === "exists") skipped += 1;
    else failed.push(`${file.name}: ${problem}`);
  }
  await reload();
  const parts = [`登録 ${added} 枚`];
  if (skipped) parts.push(`スキップ ${skipped} 枚`);
  if (unmatched.length) parts.push(`対応なし ${unmatched.length} 件: ${unmatched.join("、")}`);
  if (failed.length) parts.push(failed.join("、"));
  setMessage(parts.join(" / "));
}

async function singleRegister(file) {
  const card = zoomCard();
  const problem = await putImage(card.id, file, false);
  $("art-result").textContent = problem || "";
  if (problem === null) {
    play("pop");
    await reload();
  }
}

function isUnlocked() {
  return state.zoomId && $("zoom-stage").classList.contains("unlocked");
}

function bindDrop() {
  window.addEventListener("dragover", (event) => {
    if (event.dataTransfer.types.includes("Files")) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    if (state.isOffline || state.isReadonly) return;
    const files = [...event.dataTransfer.files];
    if (!files.length) return;
    if (isUnlocked()) {
      if (files.length > 1) $("art-result").textContent = "1 枚だけ登録できます";
      else singleRegister(files[0]);
      return;
    }
    if (event.target.closest && (event.target.closest("#binder") || event.target.closest("#zoom"))) bulkRegister(files);
  });
  window.addEventListener("paste", (event) => {
    if (!isUnlocked() || state.isOffline || state.isReadonly) return;
    const image = [...event.clipboardData.files].find((file) => file.type.startsWith("image/"));
    if (image) {
      event.preventDefault();
      singleRegister(image);
    }
  });
}

// ---- フォルダの編集
let draft = [];

function renderFolderSheet() {
  const list = $("folder-list");
  list.replaceChildren();
  draft.forEach((folder, index) => {
    const row = document.createElement("li");
    const name = document.createElement("input");
    name.value = folder.name;
    name.maxLength = 32;
    name.setAttribute("aria-label", "名前");
    name.addEventListener("change", () => {
      folder.name = name.value.trim() || folder.name;
      commitFolders();
    });
    const color = document.createElement("input");
    color.type = "color";
    color.value = folder.color;
    color.setAttribute("aria-label", "色");
    color.addEventListener("change", () => {
      folder.color = color.value;
      commitFolders();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "削除";
    remove.addEventListener("click", () => {
      draft.splice(index, 1);
      commitFolders();
    });
    if (folder.id === "uncategorized") {
      name.disabled = true;
      remove.disabled = true;
    }
    row.append(name, color, remove);
    list.append(row);
  });
}

async function commitFolders() {
  const ok = await saveAlbum({ folders: draft });
  $("folder-result").textContent = ok ? "" : state.message;
  await reload();
  draft = folders().map((f) => ({ ...f }));
  renderFolderSheet();
}

function openFolders() {
  draft = folders().map((f) => ({ ...f }));
  $("folder-result").textContent = "";
  renderFolderSheet();
  $("folders").hidden = false;
}

// ---- 配線
function bind() {
  $("search").addEventListener("input", () => {
    state.page = 0;
    render();
  });
  for (const id of ["kind", "art", "sort"]) {
    $(id).addEventListener("change", () => {
      state.page = 0;
      render();
    });
  }
  $("prev").addEventListener("click", () => turn(-1));
  $("next").addEventListener("click", () => turn(1));
  $("sound").addEventListener("click", async () => {
    const next = !state.album.sound;
    if (await saveAlbum({ sound: next })) {
      setSound(next);
      $("sound").setAttribute("aria-pressed", String(next));
    }
  });
  $("folders-open").addEventListener("click", openFolders);
  $("folders-close").addEventListener("click", () => {
    $("folders").hidden = true;
  });
  $("folder-add").addEventListener("click", () => {
    if (draft.length >= 16) {
      $("folder-result").textContent = "フォルダは 16 個まで";
      return;
    }
    draft.splice(draft.length - 1, 0, { id: `f${Date.now().toString(36)}`, name: "新しいフォルダ", color: "#C3A6E6", order: draft.length });
    commitFolders();
  });
  $("zoom").addEventListener("click", (event) => {
    if (event.target === $("zoom")) closeZoom();
  });
  bindZoomStage();
  bindBook();
  $("album-close").addEventListener("click", closeAlbum);
  $("pack").addEventListener(
    "click",
    (event) => {
      const button = $("album-close").getBoundingClientRect();
      const isOnButton = event.clientX >= button.left && event.clientX <= button.right && event.clientY >= button.top && event.clientY <= button.bottom;
      if (!isOnButton || !activePack) return;
      event.stopPropagation();
      const pack = activePack;
      pack.escape();
      pack.finished.then(closeAlbum);
    },
    true,
  );
  bindCoverFold();
  bindBar();
  window.addEventListener("keydown", (event) => {
    if (activePack) {
      if (event.key === "Escape") activePack.escape();
      return;
    }
    if (albumState !== "open") return;
    if (event.key === "Escape") {
      if (!$("folders").hidden) $("folders").hidden = true;
      else if (state.zoomId) closeZoom();
    }
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
    if (!state.zoomId && !typing && event.key === "ArrowRight") turn(1);
    if (!state.zoomId && !typing && event.key === "ArrowLeft") turn(-1);
  });
  window.addEventListener("resize", layout);
  bindDrop();
}

bind();
reload().then(() => {
  maybeShowPack();
  poll();
  setInterval(poll, 5000);
});
