const FILES = { flip: "/assets/flip.mp3", slide: "/assets/slide.mp3", pop: "/assets/pop.mp3", turn: "/assets/turn.mp3", click: "/assets/click.mp3" };
let isOn = true;

export function setSound(value) {
  isOn = value;
}

export function play(name) {
  if (!isOn || !FILES[name]) return;
  const audio = new Audio(FILES[name]);
  audio.volume = 0.5;
  audio.play().catch(() => {});
}
