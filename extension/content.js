// 숲 방송 페이지에서 동작: 새 메시지가 오면 채팅 입력칸에 넣고 전송한다.
(() => {
const SELECTORS = [
  "#write_area",
  "textarea[placeholder*='채팅']",
  "input[placeholder*='채팅']",
  "[contenteditable='true']",
  "textarea",
];
let lastSent = 0;
const PIP_TITLE = "르니톡 PIP"; // PC 프로그램이 이 제목으로 미니창을 찾아 와우 위에 붙임
let pipOn = false, origTitle = "", lastNick = "";
const pipStyled = [];
const MIN_INTERVAL = 1500;
const pending = [];

function findInput() {
  for (const sel of SELECTORS) {
    const el = document.querySelector(sel);
    if (el && el.offsetParent !== null) return el;
  }
  return null;
}

function findSendButton(input) {
  const root = input.closest("form, section, div[class*='chat'], div[id*='chat']") || document;
  const cands = root.querySelectorAll("button, a");
  for (const b of cands) {
    const t = (b.innerText || b.getAttribute("aria-label") || b.id || b.className || "").toString();
    if (/전송|send/i.test(t)) return b;
  }
  return document.querySelector("#btn_send");
}

function sendChat(text) {
  const input = findInput();
  if (!input) {
    console.warn("[르니톡] 채팅 입력칸을 못 찾음");
    return false;
  }
  input.focus();
  if ("value" in input && input.tagName !== "DIV") {
    input.value = "";
  } else {
    input.textContent = "";
  }
  document.execCommand("insertText", false, text);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  const btn = findSendButton(input);
  if (btn) {
    btn.click();
  } else {
    const opts = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true };
    input.dispatchEvent(new KeyboardEvent("keydown", opts));
    input.dispatchEvent(new KeyboardEvent("keypress", opts));
    input.dispatchEvent(new KeyboardEvent("keyup", opts));
  }
  console.log("[르니톡] 보냄:", text);
  return true;
}

function flush() {
  if (!pending.length) return;
  if (Date.now() - lastSent < MIN_INTERVAL) return;
  const msg = pending.shift();
  sendChat(msg);
  lastSent = Date.now();
}

// ===== 미니창 모드: 영상만 창 가득 보이게 (채팅 입력칸은 화면 밖으로 옮겨 둬서 전송은 계속 됨) =====
function mark(el, css) {
  if (el.dataset.ltPip === undefined) el.dataset.ltPip = el.getAttribute("style") || "";
  el.setAttribute("style", el.dataset.ltPip + ";" + css);
  pipStyled.push(el);
}

function clearPipStyle() {
  while (pipStyled.length) {
    const el = pipStyled.pop();
    if (el.dataset.ltPip !== undefined) {
      if (el.dataset.ltPip) el.setAttribute("style", el.dataset.ltPip); else el.removeAttribute("style");
      delete el.dataset.ltPip;
    }
  }
}

function applyPipStyle() {
  const v = document.querySelector("video");
  if (!v) return;
  if (v.dataset.ltPip !== undefined) return; // 이미 적용됨
  clearPipStyle();
  const OFF = "position:absolute!important;left:-30000px!important;top:0!important;opacity:0!important;pointer-events:none!important";
  const FILL = "position:static!important;transform:none!important;margin:0!important;padding:0!important;" +
    "width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;" +
    "min-width:0!important;min-height:0!important;overflow:hidden!important";
  let el = v;
  while (el && el !== document.documentElement) {
    const p = el.parentElement;
    if (!p) break;
    for (const sib of p.children) {
      if (sib !== el && !["SCRIPT", "STYLE", "LINK", "META"].includes(sib.tagName)) mark(sib, OFF);
    }
    if (el !== v) mark(el, FILL);
    el = p;
  }
  mark(v, "position:fixed!important;left:0!important;top:0!important;width:100vw!important;height:100vh!important;" +
    "object-fit:contain!important;background:#000!important;z-index:2147483647!important");
  mark(document.documentElement, "overflow:hidden!important");
}

function setPip(on) {
  if (on === pipOn) return;
  pipOn = on;
  if (on) {
    origTitle = document.title;
    applyPipStyle();
  } else {
    clearPipStyle();
    if (document.title.startsWith(PIP_TITLE)) document.title = origTitle;
  }
}

function keepPip() {
  if (!pipOn) return;
  applyPipStyle(); // 페이지가 영상을 새로 그렸으면 다시 적용
  const want = PIP_TITLE + (lastNick ? " - " + lastNick : "");
  if (document.title !== want) document.title = want;
}

let timers = [];
function stopIfOrphaned() {
  // 확장이 다시 로드되면 이 스크립트는 끊긴 상태가 됨 → 스스로 멈추고 새 스크립트에 맡김
  if (!chrome.runtime || !chrome.runtime.id) {
    timers.forEach(clearInterval);
    return true;
  }
  return false;
}

function poll() {
  if (stopIfOrphaned()) return;
  try {
    const seg = location.pathname.split("/").filter(Boolean);
    const bj = (seg[0] || "").toLowerCase();
    // 방송 중이면 주소가 /아이디/방송번호, 꺼져 있으면 /아이디/null (입력칸은 꺼져 있어도 보임)
    const live = /^\d+$/.test(seg[1] || "") ? 1 : 0;
    // 탭 제목 "르니 - SOOP" 에서 스트리머 닉네임 (닉네임으로 연결할 때 쓰임)
    // (방송이 꺼져 있으면 제목에 아이디가 나와서 방송 중일 때만)
    const title = document.title || "";
    if (!title.startsWith(PIP_TITLE)) {
      const m = title.match(/^(.+?)\s+-\s+SOOP\s*$/i);
      lastNick = live && m ? m[1].trim() : "";
    }
    const nick = live ? lastNick : "";
    // 미니창일 때: 크롬 창 테두리/제목줄 두께(실제 픽셀) → 프로그램이 그 부분을 잘라내고 영상만 보이게 붙임
    const dpr = window.devicePixelRatio || 1;
    const ui = pipOn ? [Math.round(Math.max(0, window.outerHeight - window.innerHeight) * dpr),
                        Math.round(Math.max(0, window.outerWidth - window.innerWidth) / 2 * dpr)] : null;
    chrome.runtime.sendMessage({ type: "poll", bj, nick, live, ui }, (res) => {
      if (chrome.runtime.lastError) return;
      if (res && res.messages) pending.push(...res.messages);
      if (res && typeof res.pipMode === "boolean") setPip(res.pipMode);
      keepPip();
    });
  } catch (e) {
    timers.forEach(clearInterval); // 확장이 다시 로드됨 → 이 스크립트는 종료
  }
}

timers.push(setInterval(poll, 700));
timers.push(setInterval(flush, 300));
console.log("[르니톡] 확장 프로그램 동작 중");
})();
