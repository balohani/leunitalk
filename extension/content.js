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
    const m = (document.title || "").match(/^(.+?)\s+-\s+SOOP\s*$/i);
    const nick = live && m ? m[1].trim() : "";
    chrome.runtime.sendMessage({ type: "poll", bj, nick, live }, (res) => {
      if (chrome.runtime.lastError) return;
      if (res && res.messages) pending.push(...res.messages);
      // 르니톡에서 방송을 바꾸면 새 탭 대신 이 탭을 그 방송으로 이동
      if (res && res.navigate && /^[a-z0-9_]+$/i.test(res.navigate) && res.navigate.toLowerCase() !== bj) {
        timers.forEach(clearInterval);
        location.href = "https://play.sooplive.co.kr/" + res.navigate;
      }
    });
  } catch (e) {
    timers.forEach(clearInterval); // 확장이 다시 로드됨 → 이 스크립트는 종료
  }
}

timers.push(setInterval(poll, 700));
timers.push(setInterval(flush, 300));
console.log("[르니톡] 확장 프로그램 동작 중");
})();
