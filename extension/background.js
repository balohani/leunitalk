// 르니톡 확장 백그라운드
// - 숲 탭(content.js)의 요청을 받아 내 PC의 르니톡 프로그램에서 새 메시지를 가져온다.
// - 르니톡 프로그램이 새 버전이면(확장 파일도 같이 갱신됨) 확장을 스스로 다시 로드한다.
const SOOP_URLS = ["https://play.sooplive.co.kr/*", "https://play.sooplive.com/*"];

async function maybeReload(serverVersion) {
  // 크롬 웹스토어에서 설치한 확장(update_url 있음)은 크롬이 알아서 업데이트하니 스스로 다시 로드하지 않음
  if (chrome.runtime.getManifest().update_url) return;
  const mine = chrome.runtime.getManifest().version;
  if (!serverVersion || serverVersion === mine) return;
  const { reloadedFor } = await chrome.storage.local.get("reloadedFor");
  if (reloadedFor === serverVersion) return; // 같은 버전으로 한 번만 시도 (무한 재시작 방지)
  await chrome.storage.local.set({ reloadedFor: serverVersion });
  chrome.runtime.reload();
}

// 방송 미니창: 보던 방송 탭을 작은 팝업 창(제목줄만 있는 창)으로 옮긴다. 위치/맨 위 고정은 PC 프로그램이 맞춤.
// 끌 때는 탭을 다시 옮기지 않고(팝업 창 탭은 크롬 규칙상 못 옮김 → 옮기려면 새로고침+광고) 그 창을 크게 키워
// 보통 방송 화면으로 되돌린다. 새로고침 없음.
const PIP_SIZE = { width: 480, height: 300 };

async function pipTabs() {
  const { pipTabs = [] } = await chrome.storage.local.get("pipTabs");
  return pipTabs;
}

async function setPipTab(id, on) {
  const list = (await pipTabs()).filter((x) => x !== id);
  if (on) list.push(id);
  await chrome.storage.local.set({ pipTabs: list });
}

chrome.tabs.onRemoved.addListener((id) => { setPipTab(id, false); });

// 미니창에서 되돌아갈 창: 미니창으로 빼기 전에 있던 창, 없으면 미니창이 아닌 아무 일반 창
async function homeWindow(exceptWindowId) {
  const { pipHome } = await chrome.storage.local.get("pipHome");
  if (pipHome && pipHome.windowId !== exceptWindowId) {
    try { await chrome.windows.get(pipHome.windowId); return pipHome; } catch (e) {}
  }
  const wins = (await chrome.windows.getAll({ windowTypes: ["normal"] })).filter((w) => w.id !== exceptWindowId);
  return wins.length ? { windowId: wins[0].id } : null;
}

// 탭 바꾸기: 페이지 안에서 주소를 바꾸면 "사이트에서 나가시겠습니까?" 같은 창에 막힐 수 있어서
// 같은 자리에 새 방송 탭을 열고 옛 탭을 닫는다 (확장이 닫는 건 그런 창이 안 뜸)
async function swapTab(tab, bj, inPip) {
  const url = "https://play.sooplive.co.kr/" + bj;
  try {
    // 팝업 창(미니창이었던 창)에는 탭을 더 못 넣음
    const popupWin = inPip || (await chrome.windows.get(tab.windowId)).type === "popup";
    if (popupWin) {
      // 미니창이면 원래 창에 새 방송을 열고 미니창은 닫음 (프로그램이 새 탭을 다시 미니창으로 뺌)
      const home = await homeWindow(tab.windowId);
      if (home) await chrome.tabs.create({ url, windowId: home.windowId, active: true });
      else await chrome.windows.create({ url, type: "normal", focused: false });
    } else {
      await chrome.tabs.create({ url, windowId: tab.windowId, index: tab.index + 1, active: tab.active });
    }
    await chrome.tabs.remove(tab.id);
  } catch (e) {}
}

async function enterPip(tab) {
  try {
    const win = await chrome.windows.get(tab.windowId);
    if (win.type === "popup") {
      // 전에 미니창으로 뺐던 창이면 그대로 다시 미니창으로 (크기/위치는 프로그램이 맞춤)
      await chrome.storage.local.set({ pipBig: { left: win.left, top: win.top, width: win.width, height: win.height } });
    } else {
      await chrome.storage.local.set({
        pipHome: { windowId: tab.windowId, index: tab.index },
        pipBig: { left: win.left, top: win.top, width: win.width, height: win.height },
      });
      await chrome.windows.create({ tabId: tab.id, type: "popup", focused: false, ...PIP_SIZE });
    }
    await setPipTab(tab.id, true);
  } catch (e) {}
}

async function exitPip(tab) {
  await setPipTab(tab.id, false);
  try {
    const { pipBig } = await chrome.storage.local.get("pipBig");
    const big = pipBig && pipBig.width > 400 ? pipBig : { width: 1280, height: 800 };
    await chrome.windows.update(tab.windowId, { ...big, state: "normal" });
  } catch (e) {}
}

chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
  if (req && req.type === "poll") {
    (async () => {
      const tab = sender && sender.tab;
      let popup = false; // 이 탭이 미니창으로 빠져 있는지
      try { if (tab) popup = (await pipTabs()).includes(tab.id); } catch (e) {}
      try {
        const r = await fetch("http://127.0.0.1:17777/poll?bj=" + encodeURIComponent(req.bj || "") +
          "&nick=" + encodeURIComponent((req.nick || "").slice(0, 40)) + "&live=" + (req.live ? 1 : 0) +
          "&pip=" + (popup ? 1 : 0) + (popup && Array.isArray(req.ui) ? "&ui=" + req.ui.join(",") : ""), { cache: "no-store" });
        const d = await r.json();
        if (tab && d.navigate && /^[a-z0-9_]+$/i.test(d.navigate)) {
          sendResponse({ ok: true, messages: [], pipMode: popup });
          swapTab(tab, d.navigate, popup);
          return;
        }
        if (tab && d.close) {
          sendResponse({ ok: true, messages: [], pipMode: popup });
          chrome.tabs.remove(tab.id).catch(() => {});
          return;
        }
        sendResponse({ ok: true, messages: d.messages || [], pipMode: popup });
        if (tab && d.pip === "on" && !popup) enterPip(tab);
        else if (tab && d.pip === "off" && popup) exitPip(tab);
        maybeReload(d.version);
      } catch (e) {
        sendResponse({ ok: false, messages: [], pipMode: popup });
      }
    })();
    return true;
  }
});

// 설치/업데이트 직후: 이미 열려 있는 숲 탭에 바로 연결 (새로고침 필요 없음)
chrome.runtime.onInstalled.addListener(async () => {
  const tabs = await chrome.tabs.query({ url: SOOP_URLS });
  for (const t of tabs) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] });
    } catch (e) {}
  }
});
