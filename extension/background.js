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

// 탭 바꾸기: 페이지 안에서 주소를 바꾸면 "사이트에서 나가시겠습니까?" 같은 창에 막힐 수 있어서
// 같은 자리에 새 방송 탭을 열고 옛 탭을 닫는다 (확장이 닫는 건 그런 창이 안 뜸)
async function swapTab(tab, bj) {
  try {
    await chrome.tabs.create({ url: "https://play.sooplive.co.kr/" + bj, windowId: tab.windowId,
                               index: tab.index + 1, active: tab.active });
    await chrome.tabs.remove(tab.id);
  } catch (e) {}
}

chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
  if (req && req.type === "poll") {
    fetch("http://127.0.0.1:17777/poll?bj=" + encodeURIComponent(req.bj || "") +
      "&nick=" + encodeURIComponent((req.nick || "").slice(0, 40)) + "&live=" + (req.live ? 1 : 0), { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        const tab = sender && sender.tab;
        if (tab && d.navigate && /^[a-z0-9_]+$/i.test(d.navigate)) {
          sendResponse({ ok: true, messages: [] });
          swapTab(tab, d.navigate);
          return;
        }
        if (tab && d.close) {
          sendResponse({ ok: true, messages: [] });
          chrome.tabs.remove(tab.id).catch(() => {});
          return;
        }
        sendResponse({ ok: true, messages: d.messages || [] });
        maybeReload(d.version);
      })
      .catch(() => sendResponse({ ok: false, messages: [] }));
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
