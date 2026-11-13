// 르니톡 확장 백그라운드
// - 숲 탭(content.js)의 요청을 받아 내 PC의 르니톡 프로그램에서 새 메시지를 가져온다.
// - 르니톡 프로그램이 새 버전이면(확장 파일도 같이 갱신됨) 확장을 스스로 다시 로드한다.
const SOOP_URLS = ["https://play.sooplive.co.kr/*", "https://play.sooplive.com/*"];

async function maybeReload(serverVersion) {
  const mine = chrome.runtime.getManifest().version;
  if (!serverVersion || serverVersion === mine) return;
  const { reloadedFor } = await chrome.storage.local.get("reloadedFor");
  if (reloadedFor === serverVersion) return; // 같은 버전으로 한 번만 시도 (무한 재시작 방지)
  await chrome.storage.local.set({ reloadedFor: serverVersion });
  chrome.runtime.reload();
}

chrome.runtime.onMessage.addListener((req, _sender, sendResponse) => {
  if (req && req.type === "poll") {
    fetch("http://127.0.0.1:17777/poll", { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
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
