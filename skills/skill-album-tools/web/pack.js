import { zoomFront, packBack } from "./card.js";

const MAX_SHOWN = 12;
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const noop = () => {};

function fanZoom(count) {
  const share = count === 1 ? 0.72 : count <= 3 ? 0.6 : 0.45;
  return Math.min((window.innerHeight * share) / 546, (window.innerWidth * 0.8) / 390);
}

function fanTransform(zoom, index, count) {
  const width = 390 * zoom;
  const step = count > 1 ? Math.min(width * 0.72, (window.innerWidth - width - 60) / (count - 1)) : 0;
  const tilt = count > 1 ? Math.min(7, 44 / count) : 0;
  const offset = index - (count - 1) / 2;
  return `translate(${offset * step}px, ${offset * offset * 5}px) rotate(${offset * tilt}deg)`;
}

function bigScale(zoom) {
  return Math.min((window.innerHeight - 40) / 546, (window.innerWidth - 40) / 390) / zoom;
}

export function showPack({ cards, ctx, play, markOpened, targetFor, setLocked }) {
  const layer = $("pack");
  const pack = $("pack-open");
  const host = $("pack-cards");
  const isReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ms = (value) => (isReduced ? 1 : value);
  const waiting = [...cards];
  let phase = "ready";
  let active = null;
  let trace = null;
  let wasTraced = false;
  let done;

  const finish = () => {
    layer.hidden = true;
    pack.onclick = null;
    layer.onclick = null;
    layer.onpointerdown = null;
    layer.onpointermove = null;
    layer.onpointerup = null;
    layer.onpointercancel = null;
    host.replaceChildren();
    host.classList.remove("focused");
    setLocked(false);
    done();
  };

  const slots = () => [...host.querySelectorAll(".pack-slot")];

  const settle = async (slot, animation, transform) => {
    await animation.finished;
    slot.style.transform = transform;
    animation.cancel();
  };

  const showRest = () => {
    host.querySelector(".pack-rest")?.remove();
    if (!waiting.length) return;
    const rest = document.createElement("p");
    rest.className = "pack-rest";
    rest.textContent = `ほか ${waiting.length} 枚`;
    host.append(rest);
  };

  const deal = async () => {
    phase = "busy";
    const shown = waiting.splice(0, MAX_SHOWN);
    const zoom = fanZoom(shown.length);
    const gap = ms(shown.length > 5 ? 110 : 260);
    showRest();
    for (const [index, card] of shown.entries()) {
      const slot = document.createElement("div");
      slot.className = "pack-slot";
      slot.tabIndex = 0;
      slot.setAttribute("role", "button");
      slot.setAttribute("aria-label", "カードをめくる");
      const back = packBack();
      back.style.zoom = String(zoom);
      slot.append(back);
      const transform = fanTransform(zoom, index, shown.length);
      slot.style.transform = transform;
      slot.packCard = card;
      slot.packZoom = zoom;
      slot.addEventListener("click", (event) => {
        event.stopPropagation();
        if (wasTraced) return;
        if (phase === "shown" && slot.isFace) storeFace(slot);
        else if (phase === "shown") turnOver(slot);
        else if (phase === "big") store();
      });
      slot.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        slot.click();
      });
      host.append(slot);
      slot.animate(
        [{ transform: "translateY(60%) scale(.4)", opacity: 0 }, { transform, opacity: 1 }],
        { duration: ms(360), easing: "cubic-bezier(.2,.8,.2,1)" },
      );
      if (index < 5) play("click");
      await sleep(gap);
    }
    await sleep(ms(200));
    phase = "shown";
  };

  const showFace = async (slot) => {
    slot.isFace = true;
    slot.isTurning = true;
    play("turn");
    const base = slot.style.transform;
    const edgeOn = slot.animate(
      [{ transform: `${base} perspective(1600px) rotateY(0deg)` }, { transform: `${base} perspective(1600px) rotateY(90deg)` }],
      { duration: ms(190), easing: "ease-in", fill: "forwards" },
    );
    await edgeOn.finished;
    const front = zoomFront(slot.packCard, ctx, { use: noop });
    front.style.zoom = String(slot.packZoom);
    front.querySelector(".command-use").tabIndex = -1;
    slot.replaceChildren(front);
    const faceOn = slot.animate(
      [{ transform: `${base} perspective(1600px) rotateY(-90deg)` }, { transform: `${base} perspective(1600px) rotateY(0deg)` }],
      { duration: ms(190), easing: "ease-out", fill: "forwards" },
    );
    edgeOn.cancel();
    await settle(slot, faceOn, base);
    slot.isTurning = false;
  };

  const turnOver = async (slot) => {
    if (slot.isTurning) return;
    phase = "busy";
    active = slot;
    host.classList.add("focused");
    slot.classList.add("active");
    await showFace(slot);
    const base = slot.style.transform;
    const big =`translate(0px, 0px) rotate(0deg) scale(${bigScale(slot.packZoom)})`;
    await settle(slot, slot.animate([{ transform: base }, { transform: big }], { duration: ms(380), easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" }), big);
    phase = "big";
  };

  const flyAway = (slot, delay) => {
    const from = slot.getBoundingClientRect();
    const to = targetFor(slot.packCard.id).getBoundingClientRect();
    const dx = to.left + to.width / 2 - (from.left + from.width / 2);
    const dy = to.top + to.height / 2 - (from.top + from.height / 2);
    const base = slot.style.transform;
    return slot.animate(
      [{ transform: base, opacity: 1 }, { transform: `translate(${dx}px, ${dy}px) ${base} scale(.06)`, opacity: 0.2 }],
      { duration: ms(440), delay, easing: "cubic-bezier(.5,0,.75,.4)", fill: "forwards" },
    ).finished;
  };

  const next = () => {
    const left = slots();
    if (left.length) {
      left.forEach((slot, index) => {
        slot.style.transform = fanTransform(slot.packZoom, index, left.length);
      });
      phase = "shown";
    } else if (waiting.length) {
      deal();
    } else {
      finish();
    }
  };

  const store = async () => {
    phase = "busy";
    play("slide");
    await flyAway(active, 0);
    active.remove();
    active = null;
    host.classList.remove("focused");
    next();
  };

  const storeAll = async (isEverything) => {
    phase = "busy";
    play("slide");
    host.classList.remove("focused");
    const left = slots();
    await Promise.all(left.map((slot, index) => flyAway(slot, ms(index * 60))));
    left.forEach((slot) => slot.remove());
    if (isEverything) waiting.length = 0;
    next();
  };

  const storeFace = (slot) => {
    if (slot.isTurning) return;
    if (slots().every((item) => item.isFace && !item.isTurning)) {
      storeAll(false);
      return;
    }
    active = slot;
    store();
  };

  const traceAt = (x, y) => {
    const slot = document.elementFromPoint(x, y)?.closest(".pack-slot");
    if (slot && !slot.isFace) showFace(slot);
  };

  const tear = async () => {
    phase = "busy";
    markOpened(cards.map((card) => card.id));
    play("flip");
    const top = pack.querySelector(".pack-top");
    const body = pack.querySelector(".pack-body");
    top.animate(
      [{ transform: "none", opacity: 1 }, { transform: "translate(70px, -90px) rotate(16deg)", opacity: 0 }],
      { duration: ms(320), easing: "ease-out", fill: "forwards" },
    );
    await body.animate(
      [{ transform: "none", opacity: 1 }, { transform: "translateY(45%)", opacity: 1, offset: 0.7 }, { transform: "translateY(70%)", opacity: 0 }],
      { duration: ms(560), delay: ms(120), easing: "ease-in", fill: "forwards" },
    ).finished;
    pack.hidden = true;
    pack.getAnimations({ subtree: true }).forEach((animation) => animation.cancel());
    await deal();
  };

  $("pack-count").textContent = `${cards.length} 枚`;
  host.replaceChildren();
  host.classList.remove("focused");
  pack.hidden = false;
  layer.hidden = false;
  setLocked(true);
  pack.onclick = (event) => {
    event.stopPropagation();
    if (phase === "ready") tear();
  };
  layer.onclick = () => {
    if (wasTraced) return;
    if (phase === "ready") finish();
    else if (phase === "big") store();
    else if (phase === "shown" && !slots().some((slot) => slot.isTurning)) storeAll(false);
  };
  layer.onpointerdown = (event) => {
    wasTraced = false;
    trace = phase === "shown" ? { x: event.clientX, y: event.clientY, isMoving: false } : null;
  };
  layer.onpointermove = (event) => {
    if (!trace || phase !== "shown") return;
    if (!trace.isMoving && Math.hypot(event.clientX - trace.x, event.clientY - trace.y) < 6) return;
    if (!trace.isMoving) {
      trace.isMoving = true;
      wasTraced = true;
      traceAt(trace.x, trace.y);
    }
    const dx = event.clientX - trace.x;
    const dy = event.clientY - trace.y;
    const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / 24));
    for (let step = 1; step <= steps; step += 1) traceAt(trace.x + (dx * step) / steps, trace.y + (dy * step) / steps);
    trace.x = event.clientX;
    trace.y = event.clientY;
  };
  layer.onpointerup = () => {
    trace = null;
  };
  layer.onpointercancel = () => {
    trace = null;
  };
  pack.focus({ preventScroll: true });

  return {
    finished: new Promise((resolve) => {
      done = resolve;
    }),
    escape() {
      if (phase === "ready") finish();
      else if (phase === "shown" || phase === "big") storeAll(true);
    },
  };
}
