# 르니톡 (배포용): 와우 애드온 자동 설치 + 와우 화면 신호 읽기 + 크롬 확장 중계 + 상태 창
import json, os, queue, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

APP = "르니톡"
VERSION = "1.0.0"  # 깃허브 릴리스 빌드 때 태그(v1.2.3)로 자동 교체됨
REPO = "balohani/leunitalk"
EXE_NAME = "LeuniTalk.exe"
PORT = 17777
MAXLEN = 100


def res(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP
DATA.mkdir(parents=True, exist_ok=True)
CFG_PATH = DATA / "config.json"


def load_cfg():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(c):
    CFG_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


cfg = load_cfg()
q: "queue.Queue[str]" = queue.Queue()
log_q: "queue.Queue[str]" = queue.Queue()
status = {"wow": False, "ext_last": 0.0, "ext_bj": "", "ext_target_last": 0.0, "target_live": False,
          "live_known": "", "pip_seen": 0.0, "pip_ui": None, "wow_rect": None, "wow_hwnd": 0,
          "latest": "", "latest_url": "", "latest_notes": ""}
UPDATE_EVERY = 3 * 3600  # 켜 둔 동안 3시간마다 새 버전 확인
q_lock = threading.Lock()
MSG_TTL = 30  # 이 시간(초) 안에 방송 채팅으로 못 넘어간 말은 버림 (나중에 한꺼번에 올라가는 것 방지)
pending_nav = {}  # 보던 방송 탭을 다른 방송으로 바꾸라는 요청 {"from", "to", "t"}
pending_close = {}  # 새 탭으로 열었으니 늦게라도 응답하는 옛 방송 탭은 닫으라는 요청 {"from", "t"}
CLOSE_TTL = 600
tabs = {}  # 크롬에 열려 있는 숲 탭: 아이디 → 마지막으로 확인한 시간
DEFAULT_BJ = "leuni158"  # 르니 방송 아이디
# 숲 오픈 API 클라이언트 아이디 (developers.sooplive.com 에서 발급, 공개돼도 되는 값). 비어 있으면 닉네임은
# 크롬에 열어 둔 방송/전에 연결했던 방송에서만 찾는다.
SOOP_CLIENT_ID = "3c40846875deef224004fea0101e0b5f"
WOW_FOREVER_CATE = "00040427"  # 숲 카테고리: 월드 오브 워크래프트: 포에버
CTRL_TARGET = "\x01T"  # 애드온 → 프로그램: "이 방송으로 연결해 줘" 신호 (채팅으로는 안 나감)
CTRL_PIP = "\x01P"  # 애드온 → 프로그램: 방송 미니창 켜기/끄기 ("on|off|toggle" + "|" + 채팅창 위치)
CTRL_GEOM = "\x01G"  # 애드온 → 프로그램: 르니톡 채팅창 위치/크기 (미니창을 그 위에 붙이려고)
PIP_TITLE = "르니톡 PIP"  # 미니창으로 옮긴 방송 탭의 제목 앞부분 (프로그램이 이 창을 찾아 와우 위에 고정)
toast_q: "queue.Queue[tuple]" = queue.Queue()  # 와우 위에 잠깐 띄울 알림 (글, 종류)
pending_pip = {}  # 확장에 보낼 미니창 요청 {"want": "on"/"off", "t"}
last_toast = {}  # 같은 알림 반복 방지: 키 → 시간


def toast(text, kind="info", key=None, every=0):
    """와우 위에 잠깐 알림 띄우기 (어느 스레드에서 불러도 됨). key/every 로 같은 알림을 every 초에 한 번만."""
    if key and every:
        now = time.time()
        if now - last_toast.get(key, 0) < every:
            return
        last_toast[key] = now
    log("알림: " + text)
    toast_q.put((text, kind))


def target_bj():
    return (cfg.get("bj") or DEFAULT_BJ).strip().lower()


def nick_cache():
    c = cfg.setdefault("nicks", {})
    c.setdefault(DEFAULT_BJ, "르니")
    return c


def remember_nick(bj, nick):
    nick = (nick or "").strip()
    if bj and nick and nick_cache().get(bj) != nick:
        nick_cache()[bj] = nick
        save_cfg(cfg)


def target_label(bj=None):
    bj = bj or target_bj()
    nick = nick_cache().get(bj)
    return f"{nick}({bj})" if nick else bj


def find_live_by_nick(nick):
    """숲 공식 오픈 API(방송 리스트)에서 지금 방송 중인 스트리머를 닉네임으로 찾기 → (아이디, 닉네임) 또는 None"""
    client_id = cfg.get("soop_client_id") or SOOP_CLIENT_ID
    if not client_id:
        return None
    import urllib.request
    from urllib.parse import urlencode
    want = nick.replace(" ", "").lower()
    partial = []

    def page(n):
        url = "https://openapi.sooplive.com/broad/list?" + urlencode(
            {"client_id": client_id, "select_key": "cate", "select_value": "", "order_type": "view_cnt", "page_no": n})
        req = urllib.request.Request(url, headers={"User-Agent": "LeuniTalk", "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    try:
        first = page(1)
    except Exception as e:
        log(f"숲 방송 목록 조회 실패: {e}")
        return None
    total = int(first.get("total_cnt") or 0)
    per = int(first.get("page_block") or 60) or 60
    pages = min(150, (total + per - 1) // per)

    def scan(d):
        for b in d.get("broad") or []:
            n = (b.get("user_nick") or "").replace(" ", "").lower()
            if n == want:
                return (b.get("user_id", "").lower(), b.get("user_nick", ""))
            if want and want in n:
                partial.append((b.get("user_id", "").lower(), b.get("user_nick", ""), b.get("broad_cate_no", "")))
        return None

    hit = scan(first)
    if hit:
        return hit
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        for d in ex.map(lambda n: _safe(page, n), range(2, pages + 1)):
            hit = d and scan(d)
            if hit:
                return hit
    # 닉네임 일부만 맞으면: 하나뿐이거나, 와우 포에버 방송 중 하나뿐일 때만
    wow = [p for p in partial if p[2] == WOW_FOREVER_CATE]
    for cands in (partial, wow):
        if len(cands) == 1:
            return cands[0][:2]
    return None


def _safe(f, *a):
    try:
        return f(*a)
    except Exception:
        return None


def resolve_target(text):
    """방송 주소 / 숲 아이디 / 닉네임 → (아이디, 닉네임 또는 "", 안내 메시지 또는 "")"""
    t = (text or "").strip()
    if not t:
        return None, "", "방송 아이디나 닉네임을 넣어 주세요."
    if "sooplive" in t or "afreecatv" in t or "/" in t:
        bj = parse_bj(t)
        return bj, nick_cache().get(bj, ""), ""
    import re
    m = re.search(r"\(([A-Za-z0-9_]+)\)\s*$", t)  # "르니(leuni158)" 처럼 표시된 그대로 넣었을 때
    if m:
        return m.group(1).lower(), nick_cache().get(m.group(1).lower(), ""), ""
    key = t.replace(" ", "").lower()
    # 1) 전에 연결했거나 크롬에 열려 있는 방송의 닉네임
    for bj, nick in nick_cache().items():
        if nick.replace(" ", "").lower() == key:
            return bj, nick, ""
    # 2) 영어/숫자만 있으면 숲 아이디로 봄
    if t.isascii() and all(c.isalnum() or c == "_" for c in t):
        return t.lower(), nick_cache().get(t.lower(), ""), ""
    # 3) 지금 방송 중인 스트리머 중에서 닉네임 찾기 (숲 공식 오픈 API)
    hit = find_live_by_nick(t)
    if hit and hit[0]:
        remember_nick(*hit)
        return hit[0], hit[1], ""
    return None, "", (f"'{t}' 닉네임을 못 찾았어요. 그 방송을 크롬에서 한 번 열어 두거나, 숲 아이디/방송 주소로 연결해 주세요."
                      if not (cfg.get("soop_client_id") or SOOP_CLIENT_ID)
                      else f"'{t}' 닉네임으로 지금 방송 중인 스트리머를 못 찾았어요. 방송 중인지, 닉네임이 맞는지 확인해 주세요.")


def addon_state():
    """애드온 제목줄에 보일 방송 상태: notfound(못 찾음) / live / off / "" (모름)"""
    fail = cfg.get("last_fail") or {}
    if fail.get("t", 0) > (cfg.get("bj_time") or 0):
        return "notfound"
    if status["live_known"] == target_bj():
        return "live" if status["target_live"] else "off"
    return ""


def write_addon_target():
    """와우 애드온이 다음 접속//reload 때 읽을 '연결한 방송' 정보 파일 쓰기"""
    wd = cfg.get("wowdir")
    if not wd:
        return
    bj = target_bj()
    nick = nick_cache().get(bj, "")

    def lua(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'
    body = (f"-- 르니톡 프로그램이 자동으로 쓰는 파일 (연결한 방송)\n"
            f"SoopBridge_AppTarget = {{ id = {lua(bj)}, nick = {lua(nick)}, t = {int(cfg.get('bj_time') or 0)} }}\n"
            f"SoopBridge_AppInfo = {{ version = {lua(VERSION)}, latest = {lua(status['latest'].lstrip('vV'))} }}\n"
            f"SoopBridge_AppStatus = {{ state = {lua(addon_state())}, q = {lua((cfg.get('last_fail') or {}).get('q', ''))}, "
            f"t = {int(time.time())} }}\n")
    try:
        (Path(wd) / "Interface" / "AddOns" / "SoopBridge" / "Target.lua").write_text(body, encoding="utf-8")
    except Exception as e:
        log(f"애드온에 방송 정보 쓰기 실패: {e}")


def open_broadcast(bj):
    url = f"https://play.sooplive.co.kr/{bj}"
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "chrome", url], creationflags=0x08000000)
    except Exception:
        import webbrowser
        webbrowser.open(url)


def set_target(text, source, open_tab=True):
    """연결할 방송 바꾸기 (프로그램 창 / 와우 애드온 둘 다 여기로). 성공하면 (아이디, 닉네임), 실패면 (None, 안내)"""
    bj, nick, err = resolve_target(text)
    if not bj:
        log(err)
        cfg["last_fail"] = {"q": (text or "").strip()[:60], "t": int(time.time())}
        save_cfg(cfg)
        write_addon_target()
        if source == "와우에서":
            toast(err, "warn")
        return None, err
    old = target_bj()
    if old != bj:
        status["live_known"] = ""
    cfg["bj"] = bj
    cfg["bj_time"] = int(time.time())
    save_cfg(cfg)
    if nick:
        remember_nick(bj, nick)
    write_addon_target()
    log(f"연결할 방송: {target_label(bj)}  ({source})")
    if source == "와우에서":
        toast(f"{target_label(bj)} 방송에 연결했어요", "ok")
    now = time.time()
    if old != bj and now - status["pip_seen"] < 3:
        # 미니창으로 보던 중에 방송을 바꾸면, 새 방송 탭도 미니창으로
        pending_pip.clear(); pending_pip.update({"want": "on", "t": now})
    if pending_close.get("from") == bj:  # 다시 그 방송으로 돌아왔으면 닫지 않음
        pending_close.clear()
    if open_tab and now - tabs.get(bj, 0) > 3:  # 이미 열려 있는 방송이면 그대로
        if old != bj and now - tabs.get(old, 0) < 3:
            # 보던 방송 탭을 새 방송으로 바꿈 (새 탭을 계속 늘리지 않게).
            # 크롬이 뒤에 있는 탭을 느리게 깨우면 3초 안에 못 받아 갈 수 있음 → 그땐 새로 열고,
            # 옛 탭이 나중에라도 연락해 오면 닫게 함 (탭이 쌓이지 않게)
            nav = {"from": old, "to": bj, "t": now}
            pending_nav.clear(); pending_nav.update(nav)

            def fallback():
                time.sleep(3)
                if pending_nav.get("t") == nav["t"]:
                    pending_nav.clear()
                    pending_close.clear(); pending_close.update({"from": old, "t": time.time()})
                    open_broadcast(bj)
            threading.Thread(target=fallback, daemon=True).start()
        else:
            open_broadcast(bj)
    return bj, nick


def parse_bj(text):
    """방송 주소나 아이디에서 BJ 아이디만 뽑기 (예: https://play.sooplive.co.kr/leuni158/123 → leuni158)"""
    t = (text or "").strip()
    if "sooplive" in t or "afreecatv" in t:
        t = t.split("://", 1)[-1].split("/", 1)[-1] if "/" in t else t
    parts = [x for x in t.split("?")[0].split("/") if x]
    if parts and parts[0] == "station" and len(parts) > 1:
        parts = parts[1:]
    return parts[0].lower() if parts else ""


def log(s):
    log_q.put(time.strftime("%H:%M:%S  ") + s)


# ===== 와우 폴더 찾기 + 애드온 설치 =====
def is_forever_dir(p):
    p = Path(p)
    return p.is_dir() and (p / "Wow.exe").exists() and p.name.lower() == "_forever_"


def detect_wow():
    cands = []
    try:
        import winreg
        for root, key in [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\World of Warcraft")]:
            try:
                with winreg.OpenKey(root, key) as k:
                    ip = winreg.QueryValueEx(k, "InstallPath")[0]
                    cands.append(Path(ip).parent / "_forever_")
                    cands.append(Path(ip) / "_forever_")
            except OSError:
                pass
    except ImportError:
        pass
    for drive in "CDEFG":
        for base in [r":\Program Files (x86)\World of Warcraft", r":\Program Files\World of Warcraft",
                     r":\World of Warcraft", r":\Games\World of Warcraft", r":\Blizzard\World of Warcraft"]:
            cands.append(Path(drive + base) / "_forever_")
    for c in cands:
        if is_forever_dir(c):
            return str(c)
    return None


def install_addon(wowdir):
    dst = Path(wowdir) / "Interface" / "AddOns" / "SoopBridge"
    dst.mkdir(parents=True, exist_ok=True)
    for f in res("addon", "SoopBridge").iterdir():
        shutil.copy2(f, dst / f.name)
    return dst


def install_extension_files():
    dst = DATA / "크롬확장"
    dst.mkdir(parents=True, exist_ok=True)
    for f in res("extension").iterdir():
        shutil.copy2(f, dst / f.name)
    return dst


def set_autostart(on):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'"{sys.executable}"')
            else:
                try:
                    winreg.DeleteValue(k, APP)
                except OSError:
                    pass
        return True
    except Exception as e:
        log(f"자동 실행 설정 실패: {e}")
        return False

# ===== 와우 위 알림 / 방송 미니창 =====
def warn_if_cant_send():
    """와우에서 보낸 말이 바로 못 나가는 상황이면 와우 위에 알려 줌"""
    now = time.time()
    tgt = target_label()
    if now - status["ext_target_last"] < 3:
        if not status["target_live"]:
            toast(f"{tgt} 방송이 꺼져 있어서 채팅을 못 보내요", "warn", key="off", every=10)
    elif now - status["ext_last"] < 3:
        toast(f"크롬에 {tgt} 방송 탭이 없어요 (다른 방송이 열려 있음). 30초 안에 열면 보내요", "warn", key="notab", every=10)
    else:
        toast(f"크롬에 숲 방송 탭이 없어요. 30초 안에 {tgt} 방송을 열면 보내요", "warn", key="notab", every=10)


def save_geom(text):
    """애드온이 보낸 채팅창 위치 "x,y,w,h" (와우 화면 대비 1/10000 비율) 저장"""
    try:
        g = [int(v) for v in text.strip().split(",")[:4]]
    except ValueError:
        return
    if len(g) == 4 and all(0 <= v <= 20000 for v in g) and g != cfg.get("chat_geom"):
        cfg["chat_geom"] = g
        save_cfg(cfg)


def request_pip(mode, geom=""):
    if geom:
        save_geom(geom)
    now = time.time()
    on_now = now - status["pip_seen"] < 3
    want = {"on": "on", "켜기": "on", "off": "off", "끄기": "off"}.get(mode, "off" if on_now else "on")
    if want == "on" and on_now or want == "off" and not on_now:
        return
    if want == "on" and now - status["ext_target_last"] > 3:
        toast(f"크롬에 {target_label()} 방송 탭이 없어서 미니창을 못 켜요", "warn")
        return
    pending_pip.clear(); pending_pip.update({"want": want, "t": now})
    log("방송 미니창 " + ("켜는 중" if want == "on" else "끄는 중"))


def find_pip_window():
    u = ctypes.windll.user32
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            t = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, t, 256)
            if t.value.startswith(PIP_TITLE):
                found.append(hwnd)
        return True
    u.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else None


def chrome_insets(hwnd):
    """크롬 창에서 웹페이지가 그려지는 부분(자식 창 Chrome_RenderWidgetHostHWND)이 창 가장자리에서 얼마나
    들어가 있는지 (왼쪽, 위, 오른쪽, 아래) 실제 픽셀. 주소창/탭 줄/테두리 두께를 화면 배율과 상관없이 정확히 앎."""
    u = ctypes.windll.user32
    best = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(ch, _):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(ch, cls, 64)
        if cls.value == "Chrome_RenderWidgetHostHWND" and u.IsWindowVisible(ch):
            r = wintypes.RECT(); u.GetWindowRect(ch, ctypes.byref(r))
            best.append(((r.right - r.left) * (r.bottom - r.top), r.left, r.top, r.right, r.bottom))
        return True
    u.EnumChildWindows(hwnd, EnumProc(cb), 0)
    if not best:
        return None
    _, cl, ct, cr, cb_ = max(best)
    w = wintypes.RECT(); u.GetWindowRect(hwnd, ctypes.byref(w))
    ins = (cl - w.left, ct - w.top, w.right - cr, w.bottom - cb_)
    return ins if all(0 <= v < 400 for v in ins) else None


def pip_target(rect):
    """미니창에서 영상이 보일 자리 (x, y, 폭, 높이). 따로 옮겨 둔 자리가 있으면 거기, 아니면 르니톡 채팅창 바로 위."""
    rx, ry, rw, rh = rect
    free = cfg.get("pip_free")
    if free:
        fx, fy, fw = (v / 10000 for v in free[:3])
        vw = max(200, int(fw * rw)); vh = int(vw * 9 / 16)
        vx, vy = rx + int(fx * rw), ry + int(fy * rh)
    else:
        geom = cfg.get("chat_geom")
        fx, fy, fw, fh = ((v / 10000 for v in geom) if geom else (0.01, 0.6, 0.2, 0.15))
        vw = max(240, int(fw * rw)); vh = int(vw * 9 / 16)
        vx = rx + int(fx * rw)
        top = ry + int(fy * rh)
        vy = top - vh
        if vy < ry:  # 채팅창 위에 자리가 없으면 아래에
            vy = top + int(fh * rh)
    vx = max(rx, min(vx, rx + rw - vw))
    vy = max(ry, min(vy, ry + rh - vh))
    return vx, vy, vw, vh


def pip_loop():
    """미니창(크롬 팝업 창)을 와우 위에 맨 위로 붙여 둠 (와우가 앞에 있을 때만).
    - 크롬 제목줄/테두리는 창 영역을 잘라서 숨기고 영상만 보이게
    - 미니창을 마우스로 끌면 옮겨지고(따로 둔 자리 기억), 르니톡 채팅창 위에 다시 갖다 대면 붙음
    - 오른쪽 아래 모서리를 끌면 크기 조절 (16:9 유지)"""
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    HWND_TOPMOST, HWND_NOTOPMOST = wintypes.HWND(-1), wintypes.HWND(-2)
    SWP_NOACTIVATE = 0x0010
    last = None
    drag = None  # {"mode": "move"/"size", "mx", "my", "v": (x, y, w, h)}
    cur = None   # 지금 보이는 영상 자리
    was_down = False

    last_actual = None
    pip_hwnd = None  # 지금 미니창으로 쓰는 크롬 창 (끄면 잘라 둔 모양/맨 위 고정을 풀어 줌)

    def place(hwnd, v, front, keep_bottom=False):
        """영상 자리 v 에 맞춰 크롬 창을 놓고 영상 부분만 남게 자름. 크롬 최소 크기 때문에 더 커지면
        실제 크기에 맞춰 자르고 (keep_bottom 이면 아래쪽을 맞춰) 실제로 보이는 자리를 돌려줌."""
        nonlocal last, last_actual
        vx, vy, vw, vh = v
        # 주소창/탭 줄/테두리 두께: 크롬 창 안의 웹페이지 영역으로 직접 잼 (안 되면 확장이 알려 준 값)
        ins = chrome_insets(hwnd)
        if not ins and status["pip_ui"]:
            t, sd = status["pip_ui"]
            ins = (sd, t, sd, sd)
        il, it, ir, ib = ins or (0, 0, 0, 0)
        want = (vx, vy, vw, vh, ins, front)
        if want == last and last_actual:
            return last_actual
        wx, wy, ww, wh = vx - il, vy - it, vw + il + ir, vh + it + ib
        z = HWND_TOPMOST if front else HWND_NOTOPMOST
        u.SetWindowPos(hwnd, z, wx, wy, ww, wh, SWP_NOACTIVATE)
        r = wintypes.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
        aw, ah = r.right - r.left, r.bottom - r.top
        if aw > ww + 2 or ah > wh + 2:  # 크롬이 더 작게는 안 줄어듦 → 실제 크기에 맞춤
            vw, vh = aw - il - ir, ah - it - ib
            if keep_bottom:
                vy = vy + v[3] - vh
                u.SetWindowPos(hwnd, z, wx, vy - it, aw, ah, SWP_NOACTIVATE)
        if ins != logged.get("ins"):
            logged["ins"] = ins
            log(f"미니창 크롬 테두리(왼,위,오,아래): {ins}")
        region[0] = (il, it, il + vw, it + vh) if ins else None
        ensure_region(hwnd)
        last = want
        last_actual = (vx, vy, vw, vh)
        return last_actual

    region = [None]  # 남길 영역 (창 기준 좌표)
    logged = {}

    def ensure_region(hwnd):
        """크롬은 창 크기가 바뀌거나 앞뒤가 바뀔 때 창 모양(영역)을 스스로 되돌림 → 다르면 다시 자름"""
        want = region[0]
        if not want:
            return
        box = wintypes.RECT()
        kind = u.GetWindowRgnBox(hwnd, ctypes.byref(box))  # 0 = 영역 없음
        if kind and (box.left, box.top, box.right, box.bottom) == want:
            return
        rgn = g.CreateRectRgn(*want)
        if u.SetWindowRgn(hwnd, rgn, True):
            n = logged.get("reset", 0) + 1
            logged["reset"] = n
            if n in (2, 20, 200):
                log(f"크롬이 미니창 모양을 되돌려서 다시 자름 ({n}번째)")
        else:
            g.DeleteObject(rgn)
            if not logged.get("fail"):
                logged["fail"] = True
                log("미니창 자르기 실패 (SetWindowRgn)")

    def release():
        nonlocal pip_hwnd
        if pip_hwnd and u.IsWindow(pip_hwnd):
            u.SetWindowRgn(pip_hwnd, None, True)
            u.SetWindowPos(pip_hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | SWP_NOACTIVATE)
        pip_hwnd = None

    while True:
        time.sleep(0.02 if drag else 0.2)
        try:
            if time.time() - status["pip_seen"] > 3:
                if pip_hwnd:
                    release()
                last = cur = drag = None
                region[0] = None
                logged.clear()
                status["pip_ui"] = None  # 다음에 미니창을 켜면 다시 잼
                continue
            hwnd = find_pip_window()
            rect = status["wow_rect"]
            if not hwnd or not rect:
                continue
            if hwnd != pip_hwnd:
                release()
                pip_hwnd = hwnd
            rx, ry, rw, rh = rect
            front = u.GetForegroundWindow() in (status["wow_hwnd"], hwnd)
            down = bool(u.GetAsyncKeyState(0x01) & 0x8000)
            pt = wintypes.POINT(); u.GetCursorPos(ctypes.byref(pt))
            if down and not was_down and cur:
                vx, vy, vw, vh = cur
                if vx <= pt.x < vx + vw and vy <= pt.y < vy + vh:
                    corner = pt.x > vx + vw - 22 and pt.y > vy + vh - 22
                    drag = {"mode": "size" if corner else "move", "mx": pt.x, "my": pt.y, "v": cur, "moved": False}
            was_down = down
            if drag:
                dx, dy = pt.x - drag["mx"], pt.y - drag["my"]
                if abs(dx) + abs(dy) > 6:
                    drag["moved"] = True
                vx, vy, vw, vh = drag["v"]
                if drag["moved"]:
                    if drag["mode"] == "move":
                        vx, vy = vx + dx, vy + dy
                    else:
                        vw = max(200, min(rw, vw + dx)); vh = int(vw * 9 / 16)
                    cur = place(hwnd, (vx, vy, vw, vh), front)
                if not down:  # 놓음
                    if drag["moved"]:
                        geom = cfg.get("chat_geom")
                        snap = False
                        if geom and drag["mode"] == "move":
                            cx, cy, cw = rx + geom[0] * rw // 10000, ry + geom[1] * rh // 10000, geom[2] * rw // 10000
                            # 영상 아래쪽이 채팅창 위쪽 근처이고 가로로 겹치면 다시 붙임
                            snap = abs((vy + vh) - cy) < 45 and vx < cx + cw and vx + vw > cx
                        if snap:
                            cfg.pop("pip_free", None)
                            log("미니창을 채팅창 위에 다시 붙였어요")
                        else:
                            cfg["pip_free"] = [int((vx - rx) * 10000 / rw), int((vy - ry) * 10000 / rh), int(vw * 10000 / rw)]
                        save_cfg(cfg)
                        last = None
                    drag = None
                continue
            cur = place(hwnd, pip_target(rect), front, keep_bottom=not cfg.get("pip_free"))
            ensure_region(hwnd)
        except Exception as e:
            log(f"미니창 위치 맞추기 실패: {e}")
            drag = None
            time.sleep(3)


# ===== 와우 화면 신호 읽기 =====
# 애드온이 화면 왼쪽 위에 잠깐 그리는 색 블록을 읽어서 메시지로 되돌린다 (Windows 전용, 추가 설치 없음).
import ctypes
from ctypes import wintypes

CELL, COLS, MAXROWS = 8, 128, 6
LEVELS = (0, 85, 170, 255)


def _q(v):
    return min(range(4), key=lambda i: abs(LEVELS[i] - v))


class Screen:
    def __init__(self):
        self.u = ctypes.windll.user32
        self.g = ctypes.windll.gdi32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            self.u.SetProcessDPIAware()

    def find_wow(self):
        """와우 창 찾기: 창 종류(GxWindowClass) 또는 제목에 Warcraft/워크래프트가 들어간 보이는 창."""
        found = []
        u = self.u
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if not u.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            title = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, title, 256)
            t = title.value
            if cls.value.startswith("GxWindowClass") or "Warcraft" in t or "워크래프트" in t:
                if "와우숲채팅" not in t and "cmd" not in t.lower():
                    found.append((hwnd, t, cls.value))
            return True

        u.EnumWindows(EnumProc(cb), 0)
        found.sort(key=lambda f: 0 if f[2].startswith("GxWindowClass") else 1)
        return found[0] if found else None

    def wow_rect(self):
        f = self.find_wow()
        if not f:
            return None
        hwnd, title, cls = f
        status["wow_hwnd"] = hwnd
        if getattr(self, "_last", None) != title:
            log(f"와우 창 찾음: {title}")
            self._last = title
        r = wintypes.RECT()
        self.u.GetClientRect(hwnd, ctypes.byref(r))
        pt = wintypes.POINT(0, 0)
        self.u.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y, r.right - r.left, r.bottom - r.top

    def grab(self, x, y, w, h):
        u, g = self.u, self.g
        hdc = u.GetDC(0)
        mem = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        old = g.SelectObject(mem, bmp)
        g.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020)

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = (ctypes.c_ubyte * (w * h * 4))()
        g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0)
        g.SelectObject(mem, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mem)
        u.ReleaseDC(0, hdc)
        return buf


def pixel_reader():
    scr = Screen()
    last_seq = None
    warned = False
    log("와우 화면 신호를 지켜보는 중")
    while True:
        time.sleep(0.1)
        rect = scr.wow_rect()
        if not rect:
            status["wow"] = False
            status["wow_rect"] = None
            continue
        status["wow"] = True
        status["wow_rect"] = rect
        x0, y0, cw, ch = rect
        if ch <= 0:
            continue
        cp = CELL * ch / 768.0  # 셀 하나의 실제 픽셀 크기
        w = max(1, min(cw, int(COLS * cp) + 2))
        h = max(1, min(ch, int(MAXROWS * cp) + 2))
        try:
            buf = scr.grab(x0, y0, w, h)
        except Exception:
            continue

        def sym(i):
            cx = int(((i % COLS) + 0.5) * cp)
            cy = int(((i // COLS) + 0.5) * cp)
            if cx >= w or cy >= h:
                return None
            o = (cy * w + cx) * 4
            b, gg, r = buf[o], buf[o + 1], buf[o + 2]
            return _q(r) * 16 + _q(gg) * 4 + _q(b)

        if [sym(i) for i in range(4)] != [63, 0, 63, 0]:
            continue
        seq = sym(4)
        hi, lo = sym(5), sym(6)
        if None in (seq, hi, lo) or seq == last_seq:
            continue
        n = hi * 64 + lo
        nsym = (n * 8 + 5) // 6
        syms = [sym(7 + i) for i in range(nsym + 2)]
        if None in syms:
            continue
        bits = acc = 0
        data = bytearray()
        for v in syms[:nsym]:
            acc = (acc << 6) | v
            bits += 6
            if bits >= 8:
                bits -= 8
                data.append((acc >> bits) & 0xFF)
        data = bytes(data[:n])
        if sum(data) % 4096 != syms[nsym] * 64 + syms[nsym + 1]:
            continue
        last_seq = seq
        raw = data.decode("utf-8", errors="ignore")
        if raw.startswith(CTRL_TARGET):
            want = raw[len(CTRL_TARGET):].strip()
            log("와우에서 방송 연결 요청: " + want)
            threading.Thread(target=set_target, args=(want, "와우에서"), daemon=True).start()
            continue
        if raw.startswith(CTRL_PIP):
            mode, _, geom = raw[len(CTRL_PIP):].partition("|")
            request_pip(mode.strip(), geom)
            continue
        if raw.startswith(CTRL_GEOM):
            save_geom(raw[len(CTRL_GEOM):])
            continue
        msg = raw.strip()[:MAXLEN]
        if msg:
            log("와우에서 받음: " + msg)
            q.put((time.time(), msg))
            warn_if_cant_send()



# ===== 자동 업데이트 (깃허브 릴리스) =====
def _ver(v):
    try:
        return tuple(int(x) for x in v.lstrip("vV").split("."))
    except Exception:
        return (0,)


def update_loop(app):
    while True:
        check_update(app)
        time.sleep(UPDATE_EVERY)


BETA_FLAG = "LeuniTalk.beta"  # exe 옆에 이 파일이 있거나 config 의 channel 이 beta 면 테스트 버전도 받음


def is_beta():
    if (cfg.get("channel") or "").lower() == "beta":
        return True
    try:
        return (Path(sys.executable).parent / BETA_FLAG).exists()
    except Exception:
        return False


def fetch_release():
    """일반: 정식 릴리스 중 최신. 베타: 테스트용(prerelease) 포함 가장 높은 버전."""
    import urllib.request
    hdr = {"User-Agent": "LeuniTalk", "Accept": "application/vnd.github+json"}
    if not is_beta():
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=hdr)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=20", headers=hdr)
    with urllib.request.urlopen(req, timeout=10) as r:
        rels = [x for x in json.load(r) if not x.get("draft")]
    return max(rels, key=lambda x: _ver(x.get("tag_name", "")), default={})


def check_update(app):
    if not getattr(sys, "frozen", False):
        return  # 개발 중(파이썬 직접 실행)에는 건너뜀
    try:
        rel = fetch_release()
    except Exception as e:
        log(f"업데이트 확인 실패: {e}")
        return
    latest = rel.get("tag_name", "")
    if _ver(latest) <= _ver(VERSION):
        if not status["latest"]:
            log(f"최신 버전이에요 ({VERSION})")
        return
    url = next((a["browser_download_url"] for a in rel.get("assets", []) if a.get("name") == EXE_NAME), None)
    if not url:
        return
    first = status["latest"] != latest
    status.update(latest=latest, latest_url=url, latest_notes=rel.get("body") or "")
    write_addon_target()  # 와우 애드온도 다음 접속//reload 때 "새 버전 있어요" 알림
    if first:
        log(f"새 버전 {latest} 이 나왔어요")
        app.root.after(0, lambda: app.ask_update(latest, url, rel.get("body") or ""))


def apply_update(url):
    import urllib.request
    new = DATA / "LeuniTalk_new.exe"
    req = urllib.request.Request(url, headers={"User-Agent": "LeuniTalk"})
    with urllib.request.urlopen(req, timeout=120) as r, open(new, "wb") as f:
        shutil.copyfileobj(r, f)
    cur = Path(sys.executable)
    bat = DATA / "update.bat"
    # exe 하나짜리(onefile)는 프로세스가 둘(부모 + 지금 이 프로세스)이라, 이 프로세스가 끝나도 부모가 정리하는 동안
    # exe 파일이 잠겨 있을 수 있음 → 둘 다 끝나길 기다리고, 덮어쓰기가 실패하면 될 때까지 다시 시도
    # (전에는 덮어쓰기가 실패한 채 옛 exe 가 다시 켜져서 "또 업데이트" 가 한두 번 반복됐음)
    pids = [os.getpid()] + ([os.getppid()] if getattr(sys, "frozen", False) else [])
    wait = "".join(
        f":w{p}\r\nset /a n+=1\r\nif %n% geq 30 goto swap\r\n"
        f"tasklist /fi \"PID eq {p}\" | find \" {p} \" >nul && (timeout /t 1 /nobreak >nul & goto w{p})\r\n"
        for p in pids)
    bat.write_text(
        "@echo off\r\nset n=0\r\n" + wait +
        ":swap\r\nset m=0\r\n"
        ":try\r\n"
        f"move /y \"{new}\" \"{cur}\" >nul 2>&1 && goto run\r\n"
        "set /a m+=1\r\nif %m% geq 30 goto run\r\n"
        "timeout /t 1 /nobreak >nul\r\ngoto try\r\n"
        ":run\r\n"
        f"start \"\" \"{cur}\"\r\n"
        "del \"%~f0\"\r\n", encoding="mbcs" if os.name == "nt" else "utf-8")
    # 새로 켜질 exe가 지금 프로세스의 임시 폴더(_MEI…)를 이어받지 않게 환경을 초기화
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in list(env):
        if k.startswith("_PYI") or k.startswith("_MEI"):
            env.pop(k, None)
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x08000000, env=env)
    os._exit(0)


# ===== 크롬 확장이 메시지를 가져가는 내 PC 전용 주소 =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/poll"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            bj = (qs.get("bj", [""])[0] or "").lower()
            nick = (qs.get("nick", [""])[0] or "").strip()
            now = time.time()
            status["ext_last"] = now
            status["ext_bj"] = bj
            if bj:
                tabs[bj] = now
                if nick:
                    remember_nick(bj, nick)
            msgs = []
            live = qs.get("live", ["1"])[0] == "1"  # 옛 확장은 live 를 안 보냄 → 방송 중으로 봄
            pip = qs.get("pip", ["0"])[0] == "1"
            if pip:
                status["pip_seen"] = now
                try:
                    top, side = (int(v) for v in qs.get("ui", [""])[0].split(",")[:2])
                    # 처음 잰 값으로 고정: 창 크기를 바꿀 때마다 반올림 때문에 1픽셀씩 달라져서
                    # 그때마다 창을 다시 맞추면 미니창이 계속 커졌다 작아졌다 함
                    if status["pip_ui"] is None and 0 <= top < 300 and 0 <= side < 50:
                        # top 은 위아래 크롬 테두리 합 → 아래 테두리(≈ 옆 테두리)를 빼면 제목줄+위 테두리
                        dt, ds = (cfg.get("pip_trim") or [0, 0])[:2]  # PC마다 어긋나면 설정으로 보정
                        side2 = max(0, side + int(ds))
                        status["pip_ui"] = (max(0, top - side + int(dt)), side2)
                except ValueError:
                    pass
            if bj == target_bj():  # 연결한 방송 탭에만 메시지를 넘김
                status["ext_target_last"] = now
                if status["live_known"] == bj and status["target_live"] != live:
                    toast(f"{target_label(bj)} 방송이 " + ("시작됐어요" if live else "꺼졌어요"), "ok" if live else "warn")
                changed = status["live_known"] != bj or status["target_live"] != live
                status["target_live"] = live
                status["live_known"] = bj
                if changed:
                    write_addon_target()
                if live:
                    with q_lock:
                        while True:
                            try:
                                t0, m = q.get_nowait()
                            except queue.Empty:
                                break
                            if now - t0 <= MSG_TTL:
                                msgs.append(m)
                            else:
                                log("너무 늦어서 안 보냄: " + m)
            resp = {"messages": msgs, "version": VERSION}
            if pending_pip:
                want = pending_pip["want"]
                if now - pending_pip["t"] > 20:
                    if want == "on":
                        toast("미니창을 못 켰어요 (크롬 방송 탭이 응답이 없어요)", "warn")
                    pending_pip.clear()
                elif want == "on" and bj == target_bj() and not pip:
                    resp["pip"] = "on"; pending_pip.clear()
                elif want == "off" and pip:
                    resp["pip"] = "off"; pending_pip.clear()
            if pending_nav and bj == pending_nav.get("from"):
                resp["navigate"] = pending_nav["to"]
                log(f"보던 방송 탭을 {target_label(pending_nav['to'])} 방송으로 바꿈")
                pending_nav.clear()
            elif pending_close and bj == pending_close.get("from"):
                if now - pending_close["t"] < CLOSE_TTL and bj != target_bj():
                    resp["close"] = True
                    log(f"새 탭으로 열어서 옛 방송 탭({target_label(bj)})은 닫음")
                pending_close.clear()
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            for m in msgs:
                log("숲으로 보냄: " + m)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ===== 와우 위에 잠깐 뜨는 알림 =====
class Toast:
    """와우 창 위쪽 가운데에 몇 초 뜨는 작은 알림. 클릭이 통과되고 포커스를 뺏지 않아 게임 입력이 안 끊김."""
    COLORS = {"info": "#3b2a5c", "ok": "#1f5c3a", "warn": "#7a3a12"}

    def __init__(self, root):
        self.root = root
        w = tk.Toplevel(root); self.w = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        self.lbl = tk.Label(w, text="", fg="#ffffff", bg=self.COLORS["info"], font=("맑은 고딕", 12, "bold"),
                            padx=18, pady=9)
        self.lbl.pack()
        self.job = None
        self.hide()
        w.update_idletasks()
        try:
            u = ctypes.windll.user32
            h = self.hwnd()
            ex = u.GetWindowLongW(h, -20)
            # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_LAYERED
            u.SetWindowLongW(h, -20, ex | 0x08000000 | 0x00000080 | 0x00000020 | 0x00080000)
        except Exception as e:
            log(f"알림 창 설정 실패: {e}")

    def hwnd(self):
        try:
            return int(self.w.wm_frame(), 16)
        except Exception:
            return self.w.winfo_id()

    def hide(self):
        try:
            self.w.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.w.geometry("+-4000+-4000")

    def show(self, text, kind="info", secs=4.5):
        col = self.COLORS.get(kind, self.COLORS["info"])
        self.lbl.config(text="르니톡  " + text, bg=col)
        self.w.update_idletasks()
        tw = self.w.winfo_reqwidth()
        rect = status["wow_rect"]
        if rect:
            rx, ry, rw, rh = rect
        else:
            rx, ry = 0, 0
            rw, rh = self.w.winfo_screenwidth(), self.w.winfo_screenheight()
        x, y = rx + (rw - tw) // 2, ry + int(rh * 0.14)
        self.w.geometry(f"+{x}+{y}")
        try:
            self.w.attributes("-alpha", 0.93)
            # 맨 위로 (포커스는 안 가져감)
            ctypes.windll.user32.SetWindowPos(self.hwnd(), wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
        except Exception:
            pass
        if self.job:
            self.root.after_cancel(self.job)
        self.job = self.root.after(int(secs * 1000), self.hide)


# ===== 상태 창 =====
class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP} {VERSION}")
        try:
            root.iconbitmap(str(res("leuni.ico")))
        except Exception:
            pass
        root.geometry("520x430")
        pad = {"padx": 10, "pady": 3, "anchor": "w"}
        self.wow_lbl = tk.Label(root, text="와우: 확인 중", font=("맑은 고딕", 10)); self.wow_lbl.pack(**pad)
        self.ext_lbl = tk.Label(root, text="크롬 확장: 확인 중", font=("맑은 고딕", 10)); self.ext_lbl.pack(**pad)
        self.addon_lbl = tk.Label(root, text="애드온: -", font=("맑은 고딕", 9), fg="#555"); self.addon_lbl.pack(**pad)
        # 새 버전이 있으면 나타나는 업데이트 버튼 (알림 창에서 '아니요' 눌러도 나중에 여기서 가능)
        self.upd_btn = tk.Button(root, text="", fg="#fff", bg="#8a4fd8", activebackground="#6d3bb0",
                                 font=("맑은 고딕", 10, "bold"), command=self.update_now)

        bjrow = tk.Frame(root); bjrow.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(bjrow, text="연결할 방송", font=("맑은 고딕", 10, "bold")).pack(side="left")
        self.bj_entry = tk.Entry(bjrow, font=("맑은 고딕", 10), width=22)
        self.bj_entry.insert(0, target_label()); self.bj_entry.pack(side="left", padx=6)
        self.bj_entry.bind("<Return>", lambda e: self.connect_bj())
        tk.Button(bjrow, text="연결 + 방송 열기", command=self.connect_bj).pack(side="left")
        tk.Label(root, text="닉네임, 숲 아이디, 방송 주소 다 돼요 (예: 르니 / leuni158). 와우에서는 /르니톡 방송 닉네임",
                 font=("맑은 고딕", 8), fg="#777").pack(padx=10, anchor="w")

        row = tk.Frame(root); row.pack(fill="x", padx=10, pady=4)
        tk.Button(row, text="크롬 확장 설치 방법", command=self.guide_extension).pack(side="left")
        tk.Button(row, text="와우 폴더 다시 선택", command=self.choose_wow).pack(side="left", padx=6)
        self.auto = tk.BooleanVar(value=bool(cfg.get("autostart", False)))
        tk.Checkbutton(row, text="윈도우 시작 시 자동 실행", variable=self.auto, command=self.toggle_auto).pack(side="left")

        self.logbox = tk.Text(root, height=10, font=("맑은 고딕", 9), state="disabled"); self.logbox.pack(fill="both", expand=True, padx=10, pady=4)

        test = tk.Frame(root); test.pack(fill="x", padx=10, pady=(0, 8))
        self.entry = tk.Entry(test); self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.test_send())
        tk.Button(test, text="테스트 전송", command=self.test_send).pack(side="left", padx=4)

        self.setup_addon()
        try:
            install_extension_files()  # 확장 파일 최신화 (확장이 버전 차이를 보고 스스로 다시 로드함)
        except Exception as e:
            log(f"확장 파일 갱신 실패: {e}")
        threading.Thread(target=update_loop, args=(self,), daemon=True).start()
        if not cfg.get("ext_guided"):
            root.after(800, self.guide_extension)
        self.toast = Toast(root)
        root.after(300, self.tick)

    def setup_addon(self):
        wd = cfg.get("wowdir")
        if not wd or not is_forever_dir(wd):
            wd = detect_wow()
        if not wd:
            messagebox.showinfo(APP, "와우 포에버 설치 폴더를 못 찾았어요.\n다음 창에서 'World of Warcraft' 폴더 안의 '_forever_' 폴더를 선택해 주세요.")
            wd = filedialog.askdirectory(title="_forever_ 폴더 선택")
            if not wd or not is_forever_dir(wd):
                self.addon_lbl.config(text="애드온: 와우 포에버 폴더를 선택해 주세요 (Wow.exe 가 있는 _forever_ 폴더)", fg="#c00")
                return
        try:
            dst = install_addon(wd)
            cfg["wowdir"] = wd; save_cfg(cfg)
            write_addon_target()
            self.addon_lbl.config(text=f"애드온: 설치됨 ({dst})", fg="#555")
            log("와우 애드온(SoopBridge) 설치/업데이트 완료. 와우를 껐다 켜면 '르니톡' 창이 생겨요.")
        except PermissionError:
            self.addon_lbl.config(text="애드온: 설치 권한 없음 → 이 프로그램을 '관리자 권한으로 실행' 해 주세요", fg="#c00")

    def choose_wow(self):
        wd = filedialog.askdirectory(title="_forever_ 폴더 선택")
        if wd:
            cfg["wowdir"] = wd; save_cfg(cfg); self.setup_addon()

    def connect_bj(self):
        text = self.bj_entry.get().strip()
        if text == target_label():  # 칸을 안 고치고 누르면 지금 방송을 다시 열기
            text = target_bj()

        def work():
            bj, info = set_target(text, "프로그램에서")
            if not bj:
                self.root.after(0, lambda: messagebox.showinfo(APP, info))
        threading.Thread(target=work, daemon=True).start()

    def copy(self, text, what):
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
        log(f"{what} 복사했어요. 붙여넣기(Ctrl+V) 하세요.")

    def guide_extension(self):
        ext = install_extension_files()
        cfg["ext_guided"] = True; save_cfg(cfg)
        if getattr(self, "guide_win", None) and self.guide_win.winfo_exists():
            self.guide_win.lift(); return
        w = tk.Toplevel(self.root); self.guide_win = w
        w.title("크롬 확장 설치 방법 (처음 한 번만)")
        try:
            w.iconbitmap(str(res("leuni.ico")))
        except Exception:
            pass
        w.geometry("560x470")
        H = ("맑은 고딕", 12, "bold"); T = ("맑은 고딕", 10)

        def step(n, title, desc, buttons=()):
            f = tk.Frame(w, bd=1, relief="groove"); f.pack(fill="x", padx=12, pady=5)
            tk.Label(f, text=f"{n}. {title}", font=H, anchor="w").pack(fill="x", padx=8, pady=(6, 0))
            tk.Label(f, text=desc, font=T, fg="#444", justify="left", anchor="w", wraplength=500).pack(fill="x", padx=8, pady=(2, 4))
            if buttons:
                bf = tk.Frame(f); bf.pack(fill="x", padx=8, pady=(0, 8))
                for label, cmd in buttons:
                    tk.Button(bf, text=label, command=cmd).pack(side="left", padx=(0, 6))

        step(1, "크롬 확장 화면 열기",
             "아래 버튼을 누르고, 크롬 새 탭 주소창에 붙여넣기(Ctrl+V) → 엔터\n(보안 때문에 프로그램이 이 화면을 직접 못 열어요)",
             [("chrome://extensions 주소 복사", lambda: self.copy("chrome://extensions", "확장 화면 주소를"))])
        step(2, "'개발자 모드' 켜기", "확장 화면 오른쪽 위의 '개발자 모드' 스위치를 켜요.")
        step(3, "'압축해제된 확장 프로그램 로드' 클릭",
             "폴더 선택 창이 뜨면, 아래 버튼으로 폴더 경로를 복사해서 폴더 선택 창 맨 위 주소칸에 붙여넣기 → 엔터 → '폴더 선택'",
             [("폴더 경로 복사", lambda: self.copy(str(ext), "확장 폴더 경로를")),
              ("폴더 열어보기", lambda: os.startfile(str(ext)))])
        step(4, "끝!", "숲 방송 탭은 새로고침하지 않아도 자동으로 연결돼요.\n이 창은 언제든 '크롬 확장 설치 방법' 버튼으로 다시 열 수 있어요.")
        tk.Button(w, text="닫기", command=w.destroy, width=10).pack(pady=8)

    def update_now(self):
        if status["latest_url"]:
            log("업데이트 받는 중...")
            self.upd_btn.config(state="disabled", text="업데이트 받는 중...")
            threading.Thread(target=self._do_update, args=(status["latest_url"],), daemon=True).start()

    def ask_update(self, latest, url, notes):
        msg = f"새 버전 {latest} 이 있어요 (지금 {VERSION}).\n\n{notes[:400]}\n\n지금 업데이트할까요? 끝나면 자동으로 다시 켜져요."
        if messagebox.askyesno(APP, msg):
            log("업데이트 받는 중...")
            threading.Thread(target=self._do_update, args=(url,), daemon=True).start()

    def _do_update(self, url):
        try:
            apply_update(url)
        except Exception as e:
            log(f"업데이트 실패: {e}")

    def toggle_auto(self):
        if set_autostart(self.auto.get()):
            cfg["autostart"] = self.auto.get(); save_cfg(cfg)

    def test_send(self):
        t = self.entry.get().strip()
        if t:
            q.put((time.time(), t[:MAXLEN])); self.entry.delete(0, "end"); log("테스트: " + t)

    def expire_old(self, now):
        """방송 탭이 없거나 꺼져 있어서 못 보낸 말은 30초 지나면 버리고 알려줌"""
        with q_lock:
            items = []
            while True:
                try:
                    items.append(q.get_nowait())
                except queue.Empty:
                    break
            for t0, m in items:
                if now - t0 <= MSG_TTL:
                    q.put((t0, m))
                else:
                    log("방송 탭이 없거나 방송이 꺼져 있어서 못 보냄: " + m)
                    toast("못 보냈어요 (방송 탭이 없거나 꺼져 있음): " + m[:30], "warn")

    def tick(self):
        self.wow_lbl.config(text="와우: 연결됨 ✅" if status["wow"] else "와우: 창을 못 찾음 (와우를 창 모드/전체 창 모드로 켜 주세요)",
                            fg="#080" if status["wow"] else "#c60")
        now = time.time()
        tgt = target_label()
        if self.root.focus_get() is not self.bj_entry and self.bj_entry.get() != tgt:
            self.bj_entry.delete(0, "end"); self.bj_entry.insert(0, tgt)  # 와우에서 바꾼 방송 반영
        if now - status["ext_target_last"] < 3 and not status["target_live"]:
            txt, col = f"크롬 확장: {tgt} 탭은 열려 있지만 방송이 꺼져 있어요 (채팅을 보낼 수 없음)", "#c60"
        elif now - status["ext_target_last"] < 3:
            txt, col = f"크롬 확장: {tgt} 방송에 연결됨 ✅", "#080"
        elif now - status["ext_last"] < 3:
            txt, col = f"크롬 확장: 숲 탭은 열려 있지만 {tgt} 방송이 아니에요 (위에서 '연결 + 방송 열기')", "#c60"
        else:
            txt, col = "크롬 확장: 숲 방송 탭이 없거나 확장이 설치/켜져 있지 않아요", "#c60"
        self.ext_lbl.config(text=txt, fg=col)
        self.expire_old(now)
        if status["latest_url"] and not self.upd_btn.winfo_ismapped():
            self.upd_btn.config(text=f"⬆ 새 버전 {status['latest']} 업데이트 (지금 {VERSION})")
            self.upd_btn.pack(fill="x", padx=10, pady=(2, 4), after=self.addon_lbl)
        items = []
        while True:
            try:
                items.append(toast_q.get_nowait())
            except queue.Empty:
                break
        if items:
            self.toast.show(*items[-1])  # 한꺼번에 여러 개면 마지막 것만
        while True:
            try:
                line = log_q.get_nowait()
            except queue.Empty:
                break
            self.logbox.config(state="normal"); self.logbox.insert("end", line + "\n"); self.logbox.see("end"); self.logbox.config(state="disabled")
        self.root.after(500, self.tick)


def main():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        r = tk.Tk(); r.withdraw(); messagebox.showinfo(APP, "이미 실행 중이에요."); return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 창 좌표를 실제 화면 픽셀로 (알림/미니창 위치용)
    except Exception:
        pass
    threading.Thread(target=pixel_reader, daemon=True).start()
    threading.Thread(target=pip_loop, daemon=True).start()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
