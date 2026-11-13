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
status = {"wow": False, "ext_last": 0.0}


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
            continue
        status["wow"] = True
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
        msg = data.decode("utf-8", errors="ignore").strip()[:MAXLEN]
        if msg:
            log("와우에서 받음: " + msg)
            q.put(msg)



# ===== 자동 업데이트 (깃허브 릴리스) =====
def _ver(v):
    try:
        return tuple(int(x) for x in v.lstrip("vV").split("."))
    except Exception:
        return (0,)


def check_update(app):
    if not getattr(sys, "frozen", False):
        return  # 개발 중(파이썬 직접 실행)에는 건너뜀
    import urllib.request
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"User-Agent": "LeuniTalk", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rel = json.load(r)
    except Exception as e:
        log(f"업데이트 확인 실패: {e}")
        return
    latest = rel.get("tag_name", "")
    if _ver(latest) <= _ver(VERSION):
        log(f"최신 버전이에요 ({VERSION})")
        return
    url = next((a["browser_download_url"] for a in rel.get("assets", []) if a.get("name") == EXE_NAME), None)
    if not url:
        return
    app.root.after(0, lambda: app.ask_update(latest, url, rel.get("body") or ""))


def apply_update(url):
    import urllib.request
    new = DATA / "LeuniTalk_new.exe"
    req = urllib.request.Request(url, headers={"User-Agent": "LeuniTalk"})
    with urllib.request.urlopen(req, timeout=120) as r, open(new, "wb") as f:
        shutil.copyfileobj(r, f)
    cur = Path(sys.executable)
    bat = DATA / "update.bat"
    bat.write_text(
        "@echo off\r\n"
        f"for /l %%i in (1,1,20) do (timeout /t 1 /nobreak >nul & tasklist /fi \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul || goto swap)\r\n"
        ":swap\r\n"
        f"move /y \"{new}\" \"{cur}\" >nul\r\n"
        f"start \"\" \"{cur}\"\r\n"
        "del \"%~f0\"\r\n", encoding="mbcs" if os.name == "nt" else "utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x08000000)
    os._exit(0)


# ===== 크롬 확장이 메시지를 가져가는 내 PC 전용 주소 =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/poll"):
            status["ext_last"] = time.time()
            msgs = []
            while True:
                try:
                    msgs.append(q.get_nowait())
                except queue.Empty:
                    break
            body = json.dumps({"messages": msgs, "version": VERSION}, ensure_ascii=False).encode("utf-8")
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


# ===== 상태 창 =====
class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP} {VERSION}")
        try:
            root.iconbitmap(str(res("leuni.ico")))
        except Exception:
            pass
        root.geometry("460x360")
        pad = {"padx": 10, "pady": 3, "anchor": "w"}
        self.wow_lbl = tk.Label(root, text="와우: 확인 중", font=("맑은 고딕", 10)); self.wow_lbl.pack(**pad)
        self.ext_lbl = tk.Label(root, text="크롬 확장: 확인 중", font=("맑은 고딕", 10)); self.ext_lbl.pack(**pad)
        self.addon_lbl = tk.Label(root, text="애드온: -", font=("맑은 고딕", 9), fg="#555"); self.addon_lbl.pack(**pad)

        row = tk.Frame(root); row.pack(fill="x", padx=10, pady=4)
        tk.Button(row, text="크롬 확장 설치하기", command=self.guide_extension).pack(side="left")
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
        threading.Thread(target=check_update, args=(self,), daemon=True).start()
        if not cfg.get("ext_guided"):
            root.after(800, self.guide_extension)
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
            self.addon_lbl.config(text=f"애드온: 설치됨 ({dst})", fg="#555")
            log("와우 애드온(SoopBridge) 설치/업데이트 완료. 와우를 껐다 켜면 '숲 채팅' 창이 생겨요.")
        except PermissionError:
            self.addon_lbl.config(text="애드온: 설치 권한 없음 → 이 프로그램을 '관리자 권한으로 실행' 해 주세요", fg="#c00")

    def choose_wow(self):
        wd = filedialog.askdirectory(title="_forever_ 폴더 선택")
        if wd:
            cfg["wowdir"] = wd; save_cfg(cfg); self.setup_addon()

    def guide_extension(self):
        ext = install_extension_files()
        cfg["ext_guided"] = True; save_cfg(cfg)
        messagebox.showinfo(APP, "크롬 확장 설치 (처음 한 번만)\n\n"
                                 "1. 지금 열리는 크롬 '확장 프로그램' 화면에서 오른쪽 위 '개발자 모드'를 켜요\n"
                                 "2. '압축해제된 확장 프로그램을 로드합니다'를 누르고\n"
                                 "3. 같이 열리는 폴더(크롬확장)를 선택해요\n"
                                 "4. 보고 있던 숲 방송 탭을 새로고침(F5)\n\n"
                                 "※ 크롬 웹스토어 등록 후에는 '추가' 버튼 한 번으로 바뀔 예정이에요.")
        try:
            os.startfile(str(ext))
        except Exception:
            pass
        try:
            subprocess.Popen(["cmd", "/c", "start", "", "chrome", "chrome://extensions"], creationflags=0x08000000)
        except Exception:
            pass

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
            q.put(t[:MAXLEN]); self.entry.delete(0, "end"); log("테스트: " + t)

    def tick(self):
        self.wow_lbl.config(text="와우: 연결됨 ✅" if status["wow"] else "와우: 창을 못 찾음 (와우를 창 모드/전체 창 모드로 켜 주세요)",
                            fg="#080" if status["wow"] else "#c60")
        ext_ok = time.time() - status["ext_last"] < 3
        self.ext_lbl.config(text="크롬 확장: 연결됨 ✅ (숲 방송 탭 열림)" if ext_ok else "크롬 확장: 숲 방송 탭이 없거나 확장이 꺼져 있음",
                            fg="#080" if ext_ok else "#c60")
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
    threading.Thread(target=pixel_reader, daemon=True).start()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
