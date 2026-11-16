const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
const SPRING_IN = 0.18;
const SPRING_OUT = 0.06;
const VARS = ["--rx", "--ry", "--px", "--py", "--bx", "--by"];
let frame = null;
const active = new Set();

function clear(item) {
  item.el.classList.remove("holo-on");
  for (const name of VARS) item.target.style.removeProperty(name);
  active.delete(item);
}

function step() {
  frame = null;
  for (const item of active) {
    if (!item.el.isConnected) {
      clear(item);
      continue;
    }
    const rate = item.isOver ? SPRING_IN : SPRING_OUT;
    item.x += (item.tx - item.x) * rate;
    item.y += (item.ty - item.y) * rate;
    apply(item);
    const settled = Math.abs(item.tx - item.x) < 0.002 && Math.abs(item.ty - item.y) < 0.002;
    if (!item.isOver && settled) clear(item);
  }
  if (active.size) frame = requestAnimationFrame(step);
}

function apply(item) {
  const { target, x, y, max } = item;
  const set = target.style;
  set.setProperty("--ry", `${((x - 0.5) * 2 * max).toFixed(2)}deg`);
  set.setProperty("--rx", `${((0.5 - y) * 2 * max).toFixed(2)}deg`);
  set.setProperty("--px", `${(x * 100).toFixed(1)}%`);
  set.setProperty("--py", `${(y * 100).toFixed(1)}%`);
  set.setProperty("--bx", `${(20 + x * 60).toFixed(1)}%`);
  set.setProperty("--by", `${(20 + y * 60).toFixed(1)}%`);
}

/**
 * host の上のカーソルで el を傾ける。CSS 変数は target に置くので、
 * target の子（カードとスリーブ）が同じ傾きを受け取れる。戻り値で解除できる。
 */
export function attachHolo(el, host, max, target = el) {
  if (REDUCED.matches) return () => {};
  const controller = new AbortController();
  const { signal } = controller;
  const item = { el, target, x: 0.5, y: 0.5, tx: 0.5, ty: 0.5, max, isOver: false };
  host.addEventListener(
    "pointermove",
    (event) => {
      const rect = host.getBoundingClientRect();
      item.tx = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      item.ty = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
      item.isOver = true;
      el.classList.add("holo-on");
      active.add(item);
      if (frame === null) frame = requestAnimationFrame(step);
    },
    { signal },
  );
  host.addEventListener(
    "pointerleave",
    () => {
      item.isOver = false;
      item.tx = 0.5;
      item.ty = 0.5;
      if (frame === null && active.size) frame = requestAnimationFrame(step);
    },
    { signal },
  );
  return () => {
    controller.abort();
    clear(item);
  };
}
