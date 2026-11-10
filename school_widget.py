# -*- coding: utf-8 -*-
"""
학교 바탕화면 위젯 (School Desktop Widget)

Windows 바탕화면에 아래 3개의 위젯을 띄웁니다.
  1) 캘린더        - 월간 달력, 오늘/공휴일/일정 표시
  2) 수업시간표    - 오늘 시간표(현재 교시 강조) / 주간 시간표
  3) 오늘 학교행사 - 오늘 행사 + 다가오는 일정(D-day)

- 추가 설치가 필요 없는 순수 Python(tkinter) 프로그램입니다.
- 위젯 위에서 마우스 오른쪽 버튼 → 메뉴(편집/설정/종료)
- 위젯 윗부분(제목줄)을 드래그하면 위치를 옮길 수 있습니다.
- (선택) 나이스(NEIS) 교육정보 개방포털 API로 시간표·학사일정을 자동으로 불러옵니다.
"""

import calendar
import datetime as dt
import json
import os
import queue
import socket
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
import urllib.request
from tkinter import messagebox, ttk

APP_NAME = "SchoolWidget"
APP_TITLE = "학교 위젯"
IS_WIN = sys.platform.startswith("win")
DAYS = ["월", "화", "수", "목", "금", "토", "일"]
SCHOOL_DAYS = 5  # 월~금

# ----------------------------------------------------------------------------
# 저장 위치
# ----------------------------------------------------------------------------


def data_dir():
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, APP_NAME)
    else:
        path = os.path.join(os.path.expanduser("~"), ".school_widget")
    os.makedirs(path, exist_ok=True)
    return path


DATA_FILE = os.path.join(data_dir(), "data.json")

DEFAULT_PERIODS = [
    {"name": "1교시", "start": "09:00", "end": "09:45"},
    {"name": "2교시", "start": "09:55", "end": "10:40"},
    {"name": "3교시", "start": "10:50", "end": "11:35"},
    {"name": "4교시", "start": "11:45", "end": "12:30"},
    {"name": "5교시", "start": "13:30", "end": "14:15"},
    {"name": "6교시", "start": "14:25", "end": "15:10"},
    {"name": "7교시", "start": "15:20", "end": "16:05"},
]

SAMPLE_TIMETABLE = [
    ["국어", "수학", "영어", "과학", "체육", "음악", ""],
    ["수학", "영어", "사회", "국어", "미술", "미술", ""],
    ["영어", "과학", "국어", "수학", "창체", "", ""],
    ["사회", "국어", "수학", "체육", "과학", "영어", ""],
    ["과학", "사회", "영어", "국어", "수학", "동아리", ""],
]

DEFAULT_DATA = {
    "settings": {
        "theme": "dark",            # dark | light
        "opacity": 0.93,
        "layer": "bottom",          # bottom(바탕화면 고정) | normal | top(항상 위)
        "locked": False,
        "visible": {"calendar": True, "timetable": True, "events": True},
        "positions": {},
        "timetable_view": "today",  # today | week
        "neis": {
            "api_key": "",
            "office_code": "",
            "school_code": "",
            "school_name": "",
            "school_kind": "",
            "grade": "",
            "class_nm": "",
            "auto_timetable": False,
            "auto_events": False,
            "last_sync": "",
        },
    },
    "periods": DEFAULT_PERIODS,
    "timetable": SAMPLE_TIMETABLE,
    "events": [],
}


def _merge(defaults, loaded):
    """loaded 에 없는 키는 defaults 로 채운다(딕셔너리만 재귀)."""
    if not isinstance(defaults, dict) or not isinstance(loaded, dict):
        return loaded
    out = dict(loaded)
    for k, v in defaults.items():
        if k not in out:
            out[k] = json.loads(json.dumps(v))
        else:
            out[k] = _merge(v, out[k])
    return out


class Store:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return _merge(DEFAULT_DATA, json.load(f))
            except (OSError, ValueError):
                # 손상된 파일은 백업해두고 기본값으로 시작
                try:
                    os.replace(self.path, self.path + ".broken")
                except OSError:
                    pass
        data = json.loads(json.dumps(DEFAULT_DATA))
        data["events"].append({
            "date": dt.date.today().isoformat(),
            "time": "",
            "title": "위젯에서 오른쪽 클릭하면 메뉴가 열려요",
            "source": "manual",
        })
        return data

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # 편의 접근자
    @property
    def settings(self):
        return self.data["settings"]

    @property
    def neis(self):
        return self.data["settings"]["neis"]

    def events_on(self, date_str):
        evs = [e for e in self.data["events"] if e.get("date") == date_str]
        return sorted(evs, key=lambda e: (e.get("time") or "99:99", e.get("title", "")))

    def event_dates(self):
        return {e.get("date") for e in self.data["events"]}

    def subject(self, weekday, period_idx):
        tt = self.data["timetable"]
        if 0 <= weekday < len(tt) and 0 <= period_idx < len(tt[weekday]):
            return tt[weekday][period_idx]
        return ""


# ----------------------------------------------------------------------------
# 공휴일 (양력 고정 + 음력 공휴일은 연도별 표) / 대체공휴일 자동 계산
# 학교 재량휴업일 등 정확한 휴일은 NEIS 학사일정 동기화로 보완하세요.
# ----------------------------------------------------------------------------

FIXED_HOLIDAYS = {
    (1, 1): "신정", (3, 1): "삼일절", (5, 5): "어린이날", (6, 6): "현충일",
    (8, 15): "광복절", (10, 3): "개천절", (10, 9): "한글날", (12, 25): "성탄절",
}
# 설날·추석은 (당일) 기준 전날~다음날 3일, 부처님오신날은 1일
LUNAR = {
    2025: {"seol": (1, 29), "chuseok": (10, 6), "buddha": (5, 5)},
    2026: {"seol": (2, 17), "chuseok": (9, 25), "buddha": (5, 24)},
    2027: {"seol": (2, 7), "chuseok": (9, 15), "buddha": (5, 13)},
}
EXTRA_HOLIDAYS = {
    dt.date(2026, 6, 3): "지방선거일",
}
# 대체공휴일 적용 대상(현충일·신정 제외)
_SUBST_FIXED = {"삼일절", "어린이날", "광복절", "개천절", "한글날", "성탄절", "부처님오신날"}

_holiday_cache = {}


def korean_holidays(year):
    """{date: 이름} - 2023년 이후 대체공휴일 규칙 적용."""
    if year in _holiday_cache:
        return _holiday_cache[year]
    entries = [(dt.date(year, m, d), name) for (m, d), name in FIXED_HOLIDAYS.items()]
    lunar = LUNAR.get(year, {})
    blocks = []
    for key, name in (("seol", "설날"), ("chuseok", "추석")):
        if key in lunar:
            c = dt.date(year, *lunar[key])
            block = [c + dt.timedelta(days=k) for k in (-1, 0, 1)]
            entries += [(d, name) for d in block]
            blocks.append(block)
    if "buddha" in lunar:
        entries.append((dt.date(year, *lunar["buddha"]), "부처님오신날"))
    entries += [(d, n) for d, n in EXTRA_HOLIDAYS.items() if d.year == year]

    h, count = {}, {}
    for d, name in entries:
        h[d] = h[d] + "·" + name if d in h and name not in h[d] else h.get(d, name)
        count[d] = count.get(d, 0) + 1

    subs = set()
    for block in blocks:  # 설/추석: 일요일 또는 다른 공휴일과 겹칠 때
        if any(d.weekday() == 6 or count[d] > 1 for d in block):
            subs.add(block[-1])
    for d, name in entries:  # 국경일·어린이날·부처님오신날·성탄절: 주말 또는 겹칠 때
        if name in _SUBST_FIXED and (d.weekday() >= 5 or count[d] > 1):
            subs.add(d)
    for base in sorted(subs):
        d = base + dt.timedelta(days=1)
        while d.weekday() >= 5 or d in h:
            d += dt.timedelta(days=1)
        h[d] = "대체공휴일"
    _holiday_cache[year] = h
    return h


def holiday_name(d):
    return korean_holidays(d.year).get(d)


# ----------------------------------------------------------------------------
# NEIS 교육정보 개방포털 API (https://open.neis.go.kr)
# ----------------------------------------------------------------------------

NEIS_BASE = "https://open.neis.go.kr/hub/"
TIMETABLE_SERVICE = {
    "초등학교": "elsTimetable",
    "중학교": "misTimetable",
    "고등학교": "hisTimetable",
    "특수학교": "spsTimetable",
}
SKIP_EVENT_NAMES = {"토요휴업일"}


class NeisError(Exception):
    pass


def neis_get(service, params, api_key=""):
    rows, page = [], 1
    while True:
        q = {"Type": "json", "pIndex": page, "pSize": 1000 if api_key else 5}
        if api_key:
            q["KEY"] = api_key
        q.update({k: v for k, v in params.items() if v not in ("", None)})
        url = NEIS_BASE + service + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={"User-Agent": "SchoolWidget/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except OSError as e:
            raise NeisError("인터넷 연결을 확인하세요. (%s)" % e)
        except ValueError:
            raise NeisError("NEIS 응답을 해석할 수 없습니다.")
        if service not in data:
            result = data.get("RESULT", {})
            if result.get("CODE") == "INFO-200":  # 데이터 없음
                return rows
            raise NeisError(result.get("MESSAGE") or "알 수 없는 오류")
        block = data[service]
        total = block[0]["head"][0]["list_total_count"]
        rows.extend(block[1].get("row", []))
        if len(rows) >= total or not api_key or page >= 20:
            return rows
        page += 1


def neis_search_school(name, api_key=""):
    rows = neis_get("schoolInfo", {"SCHUL_NM": name}, api_key)
    return [{
        "office_code": r.get("ATPT_OFCDC_SC_CODE", ""),
        "school_code": r.get("SD_SCHUL_CODE", ""),
        "school_name": r.get("SCHUL_NM", ""),
        "school_kind": r.get("SCHUL_KND_SC_NM", ""),
        "address": (r.get("ORG_RDNMA") or "").strip(),
        "office_name": r.get("ATPT_OFCDC_SC_NM", ""),
    } for r in rows]


def school_week(today):
    """이번 주 월요일(주말이면 다음 주 월요일)."""
    monday = today - dt.timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += dt.timedelta(days=7)
    return monday


def neis_fetch_timetable(neis, today):
    service = TIMETABLE_SERVICE.get(neis.get("school_kind"))
    if not service:
        raise NeisError("시간표를 지원하지 않는 학교 종류입니다: %s" % neis.get("school_kind"))
    if not (neis.get("grade") and neis.get("class_nm")):
        raise NeisError("학년과 반을 입력하세요.")
    monday = school_week(today)
    friday = monday + dt.timedelta(days=4)
    rows = neis_get(service, {
        "ATPT_OFCDC_SC_CODE": neis["office_code"],
        "SD_SCHUL_CODE": neis["school_code"],
        "GRADE": neis["grade"],
        "CLASS_NM": neis["class_nm"],
        "TI_FROM_YMD": monday.strftime("%Y%m%d"),
        "TI_TO_YMD": friday.strftime("%Y%m%d"),
    }, neis.get("api_key", ""))
    grid = {}
    for r in rows:
        try:
            d = dt.datetime.strptime(r["ALL_TI_YMD"], "%Y%m%d").date()
            p = int(r["PERIO"])
        except (KeyError, ValueError):
            continue
        subj = (r.get("ITRT_CNTNT") or "").strip().lstrip("-").strip()
        grid[(d.weekday(), p)] = subj
    return grid, monday


def neis_fetch_events(neis, today):
    year = today.year if today.month >= 3 else today.year - 1  # 학년도
    start, end = dt.date(year, 3, 1), dt.date(year + 1, 2, 28)
    rows = neis_get("SchoolSchedule", {
        "ATPT_OFCDC_SC_CODE": neis["office_code"],
        "SD_SCHUL_CODE": neis["school_code"],
        "AA_FROM_YMD": start.strftime("%Y%m%d"),
        "AA_TO_YMD": end.strftime("%Y%m%d"),
    }, neis.get("api_key", ""))
    events, seen = [], set()
    for r in rows:
        name = (r.get("EVENT_NM") or "").strip()
        if not name or name in SKIP_EVENT_NAMES:
            continue
        try:
            d = dt.datetime.strptime(r["AA_YMD"], "%Y%m%d").date().isoformat()
        except (KeyError, ValueError):
            continue
        if (d, name) in seen:
            continue
        seen.add((d, name))
        events.append({"date": d, "time": "", "title": name, "source": "neis"})
    return events, start.isoformat(), end.isoformat()


# ----------------------------------------------------------------------------
# Windows 전용 기능 (ctypes) - 다른 OS 에서는 아무것도 하지 않음
# ----------------------------------------------------------------------------

def win_dpi_aware():
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def win_hwnd(widget):
    import ctypes
    return ctypes.windll.user32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT


def win_send_to_bottom(widget):
    if not IS_WIN:
        return
    try:
        import ctypes
        # HWND_BOTTOM=1, SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
        ctypes.windll.user32.SetWindowPos(win_hwnd(widget), 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    except Exception:
        pass


def win_round_corners(widget):
    if not IS_WIN:
        return
    try:
        import ctypes
        pref = ctypes.c_int(2)  # DWMWCP_ROUND (Windows 11)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            win_hwnd(widget), 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def startup_script_path():
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup", APP_NAME + ".vbs")


def startup_enabled():
    return IS_WIN and os.path.exists(startup_script_path())


def set_startup(enable):
    if not IS_WIN:
        raise OSError("Windows 에서만 지원합니다.")
    path = startup_script_path()
    if not enable:
        if os.path.exists(path):
            os.remove(path)
        return
    if getattr(sys, "frozen", False):  # PyInstaller exe
        cmd = '"%s"' % sys.executable
    else:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw
        cmd = '"%s" "%s"' % (exe, os.path.abspath(__file__))
    vbs = 'CreateObject("WScript.Shell").Run "%s", 0, False\r\n' % cmd.replace('"', '""')
    with open(path, "w", encoding="utf-16") as f:  # VBS 는 UTF-16 으로 한글 경로 지원
        f.write(vbs)


def open_folder(path):
    if IS_WIN:
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path])


# ----------------------------------------------------------------------------
# 테마 / 글꼴
# ----------------------------------------------------------------------------

THEMES = {
    "dark": {
        "bg": "#1e2230", "card": "#262b3c", "border": "#3a4158", "fg": "#e9ecf5",
        "muted": "#8a92a8", "accent": "#5b8cff", "accent_fg": "#ffffff",
        "accent_soft": "#2f3d66", "sun": "#ff6b6b", "sat": "#6ba8ff",
        "dot": "#ffc857", "now": "#3ddc97", "past": "#5d647a",
    },
    "light": {
        "bg": "#f7f8fc", "card": "#ffffff", "border": "#d9dceb", "fg": "#222634",
        "muted": "#7a8196", "accent": "#3d6df2", "accent_fg": "#ffffff",
        "accent_soft": "#e3eaff", "sun": "#e03131", "sat": "#1c7ed6",
        "dot": "#f08c00", "now": "#2b9a66", "past": "#b3b8c6",
    },
}


class Fonts:
    def __init__(self, root):
        fams = set(tkfont.families(root))
        family = "TkDefaultFont"
        for cand in ("맑은 고딕", "Malgun Gothic", "Noto Sans CJK KR", "NanumGothic",
                     "Apple SD Gothic Neo", "Noto Sans KR", "WenQuanYi Zen Hei"):
            if cand in fams:
                family = cand
                break
        self.family = family
        self.small = (family, 9)
        self.normal = (family, 10)
        self.bold = (family, 10, "bold")
        self.title = (family, 11, "bold")
        self.big = (family, 13, "bold")


def parse_hm(s):
    try:
        h, m = s.strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def fmt_date(d):
    return "%d/%d (%s)" % (d.month, d.day, DAYS[d.weekday()])


# ----------------------------------------------------------------------------
# 위젯 기본 클래스 (테두리 없는 창 + 드래그 + 오른쪽 클릭 메뉴)
# ----------------------------------------------------------------------------

class BaseWidget(tk.Toplevel):
    key = "base"
    caption = ""
    default_pos = (100, 100)

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.T = app.theme
        self.F = app.fonts
        self.overrideredirect(True)
        self.configure(bg=self.T["border"])
        self.wm_title("%s - %s" % (APP_TITLE, self.caption))

        self.outer = tk.Frame(self, bg=self.T["bg"], padx=12, pady=10)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self.header = tk.Frame(self.outer, bg=self.T["bg"])
        self.header.pack(fill="x")
        self.title_lbl = tk.Label(self.header, text=self.caption, font=self.F.title,
                                  bg=self.T["bg"], fg=self.T["fg"])
        self.title_lbl.pack(side="left")
        self.sub_lbl = tk.Label(self.header, text="", font=self.F.small,
                                bg=self.T["bg"], fg=self.T["muted"])
        self.sub_lbl.pack(side="left", padx=(6, 0))
        self.btns = tk.Frame(self.header, bg=self.T["bg"])
        self.btns.pack(side="right")

        self.body = tk.Frame(self.outer, bg=self.T["bg"])
        self.body.pack(fill="both", expand=True, pady=(8, 0))

        for w in (self.header, self.title_lbl, self.sub_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        self.build_buttons()
        self.render()

        x, y = self.app.store.settings["positions"].get(self.key, self.default_pos)
        x, y = self._clamp(x, y)
        self.geometry("+%d+%d" % (x, y))
        self.apply_window_style()

    # --- 공통 UI 도우미 ---
    def header_button(self, text, cmd, tip=None):
        b = tk.Label(self.btns, text=text, font=self.F.bold, bg=self.T["bg"],
                     fg=self.T["muted"], padx=5, cursor="hand2")
        b.pack(side="left")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(fg=self.T["accent"]))
        b.bind("<Leave>", lambda e: b.configure(fg=self.T["muted"]))
        return b

    def label(self, parent, text, font=None, fg=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=font or self.F.normal,
                        fg=fg or self.T["fg"], bg=bg or self.T["bg"], **kw)

    def clear_body(self):
        for c in self.body.winfo_children():
            c.destroy()

    def finish_render(self):
        self._bind_context(self)

    def _bind_context(self, w):
        w.bind("<Button-3>", self._popup_menu, add="+")
        w.bind("<ButtonRelease-1>", lambda e: self.after(150, self.keep_layer), add="+")
        for c in w.winfo_children():
            self._bind_context(c)

    def _popup_menu(self, event):
        self.app.show_menu(self, event.x_root, event.y_root)
        return "break"

    # --- 창 스타일 ---
    def apply_window_style(self):
        s = self.app.store.settings
        try:
            self.attributes("-alpha", max(0.3, min(1.0, float(s.get("opacity", 0.93)))))
        except tk.TclError:
            pass
        self.attributes("-topmost", s.get("layer") == "top")
        self.after(50, lambda: win_round_corners(self))
        self.keep_layer()

    def keep_layer(self):
        if self.app.store.settings.get("layer") == "bottom":
            win_send_to_bottom(self)

    # --- 드래그 ---
    def _drag_start(self, e):
        self._drag = None
        if self.app.store.settings.get("locked"):
            return
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        if getattr(self, "_drag", None):
            dx, dy = self._drag
            self.geometry("+%d+%d" % (e.x_root - dx, e.y_root - dy))

    def _drag_end(self, e):
        if getattr(self, "_drag", None):
            self.app.store.settings["positions"][self.key] = [self.winfo_x(), self.winfo_y()]
            self.app.store.save()
        self._drag = None
        self.after(150, self.keep_layer)

    def _clamp(self, x, y):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        # 다중 모니터에서 음수 좌표도 허용하되, 너무 멀리 벗어나면 기본 위치
        if not (-sw <= x <= sw * 3 and -sh <= y <= sh * 3):
            return self.default_pos
        return int(x), int(y)

    # --- 하위 클래스에서 구현 ---
    def build_buttons(self):
        pass

    def render(self):
        pass

    def tick(self, now):
        pass


# ----------------------------------------------------------------------------
# 1) 캘린더
# ----------------------------------------------------------------------------

class CalendarWidget(BaseWidget):
    key = "calendar"
    caption = "캘린더"
    default_pos = (40, 60)
    CELL_W, CELL_H = 40, 34

    def __init__(self, app):
        today = dt.date.today()
        self.year, self.month = today.year, today.month
        super().__init__(app)

    def build_buttons(self):
        self.header_button("◀", lambda: self.shift(-1))
        self.header_button("오늘", self.go_today)
        self.header_button("▶", lambda: self.shift(1))

    def shift(self, n):
        m = self.month - 1 + n
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.render()

    def go_today(self):
        t = dt.date.today()
        self.year, self.month = t.year, t.month
        self.render()

    def render(self):
        self.clear_body()
        T, F = self.T, self.F
        self.sub_lbl.configure(text="%d년 %d월" % (self.year, self.month))
        W, H = self.CELL_W, self.CELL_H
        cal = calendar.Calendar(firstweekday=6)  # 일요일 시작
        weeks = cal.monthdatescalendar(self.year, self.month)
        c = tk.Canvas(self.body, width=W * 7, height=24 + H * len(weeks),
                      bg=T["bg"], highlightthickness=0, bd=0)
        c.pack()
        heads = ["일", "월", "화", "수", "목", "금", "토"]
        for i, h in enumerate(heads):
            color = T["sun"] if i == 0 else T["sat"] if i == 6 else T["muted"]
            c.create_text(i * W + W / 2, 11, text=h, fill=color, font=F.small)
        today = dt.date.today()
        ev_dates = self.app.store.event_dates()
        self._cells = {}
        for r, week in enumerate(weeks):
            for i, d in enumerate(week):
                x0, y0 = i * W, 24 + r * H
                cx, cy = x0 + W / 2, y0 + H / 2 - 3
                other = d.month != self.month
                hol = holiday_name(d)
                if d == today:
                    c.create_oval(cx - 13, cy - 12, cx + 13, cy + 12, fill=T["accent"], outline="")
                    color = T["accent_fg"]
                elif other:
                    color = T["past"]
                elif hol or i == 0:
                    color = T["sun"]
                elif i == 6:
                    color = T["sat"]
                else:
                    color = T["fg"]
                c.create_text(cx, cy, text=str(d.day), fill=color,
                              font=F.bold if d == today else F.normal)
                if d.isoformat() in ev_dates:
                    c.create_oval(cx - 2.5, y0 + H - 7, cx + 2.5, y0 + H - 2,
                                  fill=T["dot"] if not other else T["past"], outline="")
                self._cells[(r, i)] = d
        c.bind("<Button-1>", self._click)
        c.bind("<Motion>", lambda e: self._hover(c, e))
        c.bind("<Leave>", lambda e: self._show_info(None))

        self.info = self.label(self.body, "", font=F.small, fg=T["muted"], anchor="w")
        self.info.pack(fill="x", pady=(4, 0))
        self._show_info(None)
        self.finish_render()

    def _cell_at(self, e):
        if e.y < 24:
            return None
        return self._cells.get(((e.y - 24) // self.CELL_H, e.x // self.CELL_W))

    def _hover(self, canvas, e):
        d = self._cell_at(e)
        canvas.configure(cursor="hand2" if d else "")
        self._show_info(d)

    def _show_info(self, d):
        if not hasattr(self, "info"):
            return
        if d is None:
            # 이번 달 공휴일 요약
            hols = sorted((k, v) for k, v in korean_holidays(self.year).items() if k.month == self.month)
            txt = "  ".join("%d일 %s" % (k.day, v) for k, v in hols[:4])
            self.info.configure(text=txt or "날짜를 클릭하면 일정을 추가할 수 있어요")
            return
        parts = [fmt_date(d)]
        if holiday_name(d):
            parts.append(holiday_name(d))
        evs = self.app.store.events_on(d.isoformat())
        if evs:
            parts.append(", ".join(e["title"] for e in evs[:2]) + (" 외 %d건" % (len(evs) - 2) if len(evs) > 2 else ""))
        self.info.configure(text="  ·  ".join(parts))

    def _click(self, e):
        d = self._cell_at(e)
        if d:
            self.app.open_event_editor(d)

    def tick(self, now):
        pass


# ----------------------------------------------------------------------------
# 2) 수업시간표
# ----------------------------------------------------------------------------

class TimetableWidget(BaseWidget):
    key = "timetable"
    caption = "수업시간표"
    default_pos = (40, 380)

    def build_buttons(self):
        self.view_btn = self.header_button("주간", self.toggle_view)
        self.header_button("편집", self.app.open_timetable_editor)

    def toggle_view(self):
        s = self.app.store.settings
        s["timetable_view"] = "week" if s.get("timetable_view") == "today" else "today"
        self.app.store.save()
        self.render()

    def current_state(self, now):
        """(상태문구, 현재교시 index 또는 None)"""
        wd = now.weekday()
        if wd >= SCHOOL_DAYS:
            return "주말이에요. 푹 쉬세요!", None
        if holiday_name(now.date()):
            return "오늘은 %s - 쉬는 날!" % holiday_name(now.date()), None
        periods = self.app.store.data["periods"]
        mins = now.hour * 60 + now.minute
        last = None
        for i, p in enumerate(periods):
            subj = self.app.store.subject(wd, i)
            if subj:
                last = i
        if last is None:
            return "오늘은 수업이 없어요", None
        for i, p in enumerate(periods[: last + 1]):
            s, e = parse_hm(p["start"]), parse_hm(p["end"])
            if s is None or e is None:
                continue
            subj = self.app.store.subject(wd, i) or "-"
            if s <= mins < e:
                return "지금 %s %s · %s까지 (%d분 남음)" % (p["name"], subj, p["end"], e - mins), i
            if mins < s:
                gap_start = parse_hm(periods[i - 1]["end"]) if i > 0 else None
                if gap_start is not None and s - gap_start >= 30 and mins >= gap_start:
                    kind = "점심시간"
                elif i == 0:
                    kind = "수업 전"
                else:
                    kind = "쉬는 시간"
                return "%s · 다음 %s %s (%s)" % (kind, p["name"], subj, p["start"]), None
        return "오늘 수업 끝! 수고했어요", None

    def render(self):
        self.clear_body()
        now = dt.datetime.now()
        view = self.app.store.settings.get("timetable_view", "today")
        self.view_btn.configure(text="오늘" if view == "week" else "주간")
        if view == "week" or now.weekday() >= SCHOOL_DAYS:
            self._render_week(now)
        else:
            self._render_today(now)
        self.status = self.label(self.body, "", font=self.F.small, fg=self.T["now"], anchor="w")
        self.status.pack(fill="x", pady=(8, 0))
        self._last_state = None
        self.tick(now)
        self.finish_render()

    def _render_today(self, now):
        T, F = self.T, self.F
        self.sub_lbl.configure(text=fmt_date(now.date()))
        wd = now.weekday()
        periods = self.app.store.data["periods"]
        last = -1
        for i in range(len(periods)):
            if self.app.store.subject(wd, i):
                last = i
        self.rows = []
        frame = tk.Frame(self.body, bg=T["bg"])
        frame.pack(fill="x")
        if last < 0:
            self.label(frame, "등록된 수업이 없어요", fg=T["muted"]).pack(anchor="w", pady=6)
        for i in range(last + 1):
            p = periods[i]
            row = tk.Frame(frame, bg=T["card"], padx=8, pady=4)
            row.pack(fill="x", pady=1)
            n = self.label(row, p["name"], font=F.small, fg=T["muted"], bg=T["card"], width=5, anchor="w")
            n.pack(side="left")
            s = self.label(row, self.app.store.subject(wd, i) or "-", font=F.bold, bg=T["card"], anchor="w")
            s.pack(side="left", fill="x", expand=True)
            t = self.label(row, "%s~%s" % (p["start"], p["end"]), font=F.small, fg=T["muted"], bg=T["card"])
            t.pack(side="right")
            self.rows.append((row, n, s, t, i))
        frame.configure(width=280)

    def _render_week(self, now):
        T, F = self.T, self.F
        monday = school_week(now.date())
        self.sub_lbl.configure(text="%d/%d ~ %d/%d" % (
            monday.month, monday.day, (monday + dt.timedelta(4)).month, (monday + dt.timedelta(4)).day))
        self.rows = []
        periods = self.app.store.data["periods"]
        grid = tk.Frame(self.body, bg=T["border"])
        grid.pack()
        today_wd = now.weekday() if now.weekday() < SCHOOL_DAYS else -1
        self.label(grid, "", bg=T["card"], width=3).grid(row=0, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
        for d in range(SCHOOL_DAYS):
            hl = d == today_wd
            self.label(grid, DAYS[d], font=F.bold, bg=T["accent"] if hl else T["card"],
                       fg=T["accent_fg"] if hl else T["muted"], width=6, pady=3
                       ).grid(row=0, column=d + 1, sticky="nsew", padx=(0, 1), pady=(0, 1))
        self.week_cells = {}
        for i, p in enumerate(periods):
            self.label(grid, str(i + 1), font=F.small, fg=T["muted"], bg=T["card"], pady=3
                       ).grid(row=i + 1, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            for d in range(SCHOOL_DAYS):
                hl = d == today_wd
                cell = self.label(grid, self.app.store.subject(d, i), font=F.small,
                                  bg=T["accent_soft"] if hl else T["card"], width=6, pady=3)
                cell.grid(row=i + 1, column=d + 1, sticky="nsew", padx=(0, 1), pady=(0, 1))
                self.week_cells[(d, i)] = cell

    def tick(self, now):
        text, cur = self.current_state(now)
        if (text, cur) == self._last_state:
            return
        self._last_state = (text, cur)
        self.status.configure(text=text)
        T = self.T
        mins = now.hour * 60 + now.minute
        periods = self.app.store.data["periods"]
        is_school = now.weekday() < SCHOOL_DAYS and not holiday_name(now.date())
        for row, n, s, t, i in getattr(self, "rows", []):
            end = parse_hm(periods[i]["end"]) if i < len(periods) else None
            if i == cur:
                bg, fg, mfg = T["accent"], T["accent_fg"], T["accent_fg"]
            elif is_school and end is not None and mins >= end:
                bg, fg, mfg = T["card"], T["past"], T["past"]
            else:
                bg, fg, mfg = T["card"], T["fg"], T["muted"]
            row.configure(bg=bg)
            n.configure(bg=bg, fg=mfg)
            s.configure(bg=bg, fg=fg)
            t.configure(bg=bg, fg=mfg)
        if hasattr(self, "week_cells") and self.app.store.settings.get("timetable_view") == "week":
            wd = now.weekday()
            for (d, i), cell in self.week_cells.items():
                if not cell.winfo_exists():
                    continue
                if d == wd and i == cur:
                    cell.configure(bg=T["accent"], fg=T["accent_fg"])
                else:
                    cell.configure(bg=T["accent_soft"] if d == wd else T["card"], fg=T["fg"])


# ----------------------------------------------------------------------------
# 3) 오늘 학교행사
# ----------------------------------------------------------------------------

class EventsWidget(BaseWidget):
    key = "events"
    caption = "오늘 학교행사"
    default_pos = (360, 60)
    UPCOMING_DAYS = 30
    UPCOMING_MAX = 5

    def build_buttons(self):
        self.header_button("+", lambda: self.app.open_event_editor(dt.date.today()))

    def render(self):
        self.clear_body()
        T, F = self.T, self.F
        today = dt.date.today()
        self.sub_lbl.configure(text=fmt_date(today))
        store = self.app.store

        box = tk.Frame(self.body, bg=T["card"], padx=10, pady=8)
        box.pack(fill="x")
        hol = holiday_name(today)
        if hol:
            self.label(box, "공휴일 · " + hol, font=F.bold, fg=T["sun"], bg=T["card"], anchor="w").pack(fill="x")
        evs = store.events_on(today.isoformat())
        if not evs and not hol:
            self.label(box, "오늘은 예정된 행사가 없어요", fg=T["muted"], bg=T["card"], anchor="w").pack(fill="x")
        for e in evs:
            line = tk.Frame(box, bg=T["card"])
            line.pack(fill="x", pady=1)
            self.label(line, "●", font=F.small, fg=T["dot"], bg=T["card"]).pack(side="left")
            if e.get("time"):
                self.label(line, e["time"], font=F.small, fg=T["muted"], bg=T["card"]).pack(side="left", padx=(4, 0))
            self.label(line, e["title"], font=F.bold, bg=T["card"], anchor="w",
                       wraplength=230, justify="left").pack(side="left", padx=(4, 0), fill="x")

        upcoming = []
        for e in store.data["events"]:
            try:
                d = dt.date.fromisoformat(e["date"])
            except (KeyError, ValueError):
                continue
            if today < d <= today + dt.timedelta(days=self.UPCOMING_DAYS):
                upcoming.append((d, e.get("time") or "", e["title"]))
        upcoming.sort()
        self.label(self.body, "다가오는 일정", font=F.small, fg=T["muted"], anchor="w").pack(fill="x", pady=(10, 2))
        if not upcoming:
            self.label(self.body, "앞으로 %d일간 일정이 없어요" % self.UPCOMING_DAYS,
                       font=F.small, fg=T["past"], anchor="w").pack(fill="x")
        for d, _, title in upcoming[: self.UPCOMING_MAX]:
            line = tk.Frame(self.body, bg=T["bg"])
            line.pack(fill="x", pady=1)
            dd = (d - today).days
            self.label(line, "D-%d" % dd, font=F.bold, width=5, anchor="w",
                       fg=T["accent"] if dd <= 3 else T["muted"]).pack(side="left")
            self.label(line, fmt_date(d), font=F.small, fg=T["muted"], width=9, anchor="w").pack(side="left")
            self.label(line, title, font=F.normal, anchor="w").pack(side="left", fill="x")
        if len(upcoming) > self.UPCOMING_MAX:
            self.label(self.body, "외 %d건" % (len(upcoming) - self.UPCOMING_MAX), font=F.small,
                       fg=T["muted"], anchor="e").pack(fill="x")
        spacer = tk.Frame(self.body, bg=T["bg"], width=290, height=1)
        spacer.pack()
        self.finish_render()


# ----------------------------------------------------------------------------
# 편집 창들 (일반 창)
# ----------------------------------------------------------------------------

class Dialog(tk.Toplevel):
    def __init__(self, app, title):
        super().__init__(app.root)
        self.app = app
        self.title("%s - %s" % (APP_TITLE, title))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("+%d+%d" % ((sw - w) // 2, (sh - h) // 3))
        self.lift()
        self.focus_force()


class TimetableEditor(Dialog):
    def __init__(self, app):
        super().__init__(app, "시간표 편집")
        store = app.store
        self.periods = [dict(p) for p in store.data["periods"]]
        self.tt = [list(r) for r in store.data["timetable"]]
        ttk.Label(self.body, text="과목과 교시별 시간을 입력하세요. (시간 형식 09:00)").pack(anchor="w")
        self.grid_frame = ttk.Frame(self.body)
        self.grid_frame.pack(pady=8)
        bar = ttk.Frame(self.body)
        bar.pack(fill="x")
        ttk.Button(bar, text="교시 추가", command=self.add_period).pack(side="left")
        ttk.Button(bar, text="마지막 교시 삭제", command=self.del_period).pack(side="left", padx=4)
        ttk.Button(bar, text="전체 지우기", command=self.clear_all).pack(side="left")
        ttk.Button(bar, text="취소", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="저장", command=self.save).pack(side="right", padx=4)
        self.build_grid()
        self.center()

    def _collect(self):
        for i, row in enumerate(self.entries):
            name, start, end = row[0].get().strip(), row[1].get().strip(), row[2].get().strip()
            self.periods[i] = {"name": name or "%d교시" % (i + 1), "start": start, "end": end}
            for d in range(SCHOOL_DAYS):
                while len(self.tt) <= d:
                    self.tt.append([])
                while len(self.tt[d]) <= i:
                    self.tt[d].append("")
                self.tt[d][i] = row[3 + d].get().strip()
        n = len(self.periods)
        self.tt = [(r + [""] * n)[:n] for r in (self.tt + [[]] * SCHOOL_DAYS)[:SCHOOL_DAYS]]

    def build_grid(self):
        for c in self.grid_frame.winfo_children():
            c.destroy()
        heads = ["교시", "시작", "종료"] + DAYS[:SCHOOL_DAYS]
        for j, h in enumerate(heads):
            ttk.Label(self.grid_frame, text=h, anchor="center").grid(row=0, column=j, padx=2, pady=2)
        self.entries = []
        for i, p in enumerate(self.periods):
            row = []
            for j, (val, w) in enumerate(((p["name"], 7), (p["start"], 6), (p["end"], 6))):
                e = ttk.Entry(self.grid_frame, width=w, justify="center")
                e.insert(0, val)
                e.grid(row=i + 1, column=j, padx=2, pady=2)
                row.append(e)
            for d in range(SCHOOL_DAYS):
                e = ttk.Entry(self.grid_frame, width=9, justify="center")
                v = self.tt[d][i] if d < len(self.tt) and i < len(self.tt[d]) else ""
                e.insert(0, v)
                e.grid(row=i + 1, column=3 + d, padx=2, pady=2)
                row.append(e)
            self.entries.append(row)

    def add_period(self):
        self._collect()
        n = len(self.periods)
        last_end = parse_hm(self.periods[-1]["end"]) if self.periods else None
        if last_end is None:
            last_end = 8 * 60 + 50
        s, e = last_end + 10, last_end + 55
        self.periods.append({"name": "%d교시" % (n + 1), "start": "%02d:%02d" % divmod(s, 60),
                             "end": "%02d:%02d" % divmod(e, 60)})
        self._collect_pad()
        self.build_grid()

    def _collect_pad(self):
        n = len(self.periods)
        self.tt = [(r + [""] * n)[:n] for r in self.tt]

    def del_period(self):
        if len(self.periods) <= 1:
            return
        self._collect()
        self.periods.pop()
        self._collect_pad()
        self.build_grid()

    def clear_all(self):
        if messagebox.askyesno(APP_TITLE, "모든 과목을 지울까요?", parent=self):
            for row in self.entries:
                for e in row[3:]:
                    e.delete(0, "end")

    def save(self):
        self._collect()
        for p in self.periods:
            s, e = parse_hm(p["start"]), parse_hm(p["end"])
            if s is None or e is None or s >= e:
                messagebox.showerror(APP_TITLE, "%s 의 시간을 확인하세요. (예: 09:00 ~ 09:45)" % p["name"], parent=self)
                return
            p["start"], p["end"] = "%02d:%02d" % divmod(s, 60), "%02d:%02d" % divmod(e, 60)
        self.app.store.data["periods"] = self.periods
        self.app.store.data["timetable"] = self.tt
        self.app.store.save()
        self.app.refresh()
        self.destroy()


class EventEditor(Dialog):
    def __init__(self, app, date=None):
        super().__init__(app, "학교행사 · 일정 편집")
        self.date = date or dt.date.today()
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        self.filter_var = tk.StringVar(value="day")
        ttk.Radiobutton(top, text="선택한 날짜", variable=self.filter_var, value="day",
                        command=self.reload).pack(side="left")
        ttk.Radiobutton(top, text="이번 달", variable=self.filter_var, value="month",
                        command=self.reload).pack(side="left", padx=6)
        ttk.Radiobutton(top, text="전체", variable=self.filter_var, value="all",
                        command=self.reload).pack(side="left")

        cols = ("date", "time", "title", "source")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings", height=10, selectmode="browse")
        for c, t, w in zip(cols, ("날짜", "시간", "행사명", "출처"), (95, 55, 260, 50)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "title" else "center")
        self.tree.pack(fill="both", pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        form = ttk.Frame(self.body)
        form.pack(fill="x")
        ttk.Label(form, text="날짜").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="시간(선택)").grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="행사명").grid(row=0, column=2, sticky="w")
        self.e_date = ttk.Entry(form, width=12)
        self.e_time = ttk.Entry(form, width=7)
        self.e_title = ttk.Entry(form, width=36)
        self.e_date.grid(row=1, column=0)
        self.e_time.grid(row=1, column=1, padx=4)
        self.e_title.grid(row=1, column=2, sticky="we")
        self.e_date.insert(0, self.date.isoformat())
        self.e_title.bind("<Return>", lambda e: self.add())

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="추가", command=self.add).pack(side="left")
        ttk.Button(bar, text="선택 수정", command=self.update).pack(side="left", padx=4)
        ttk.Button(bar, text="선택 삭제", command=self.delete).pack(side="left")
        ttk.Button(bar, text="닫기", command=self.destroy).pack(side="right")
        self.reload()
        self.center()
        self.e_title.focus_set()

    def _visible_events(self):
        f = self.filter_var.get()
        evs = list(enumerate(self.app.store.data["events"]))
        if f == "day":
            evs = [x for x in evs if x[1].get("date") == self.date.isoformat()]
        elif f == "month":
            prefix = self.date.strftime("%Y-%m")
            evs = [x for x in evs if str(x[1].get("date", "")).startswith(prefix)]
        return sorted(evs, key=lambda x: (x[1].get("date", ""), x[1].get("time") or "99"))

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        for idx, e in self._visible_events():
            src = "NEIS" if e.get("source") == "neis" else "직접"
            self.tree.insert("", "end", iid=str(idx), values=(e.get("date"), e.get("time", ""), e.get("title"), src))

    def on_select(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        e = self.app.store.data["events"][int(sel[0])]
        for w, v in ((self.e_date, e.get("date", "")), (self.e_time, e.get("time", "")), (self.e_title, e.get("title", ""))):
            w.delete(0, "end")
            w.insert(0, v)

    def _read_form(self):
        try:
            d = dt.date.fromisoformat(self.e_date.get().strip())
        except ValueError:
            messagebox.showerror(APP_TITLE, "날짜는 2026-03-02 형식으로 입력하세요.", parent=self)
            return None
        t = self.e_time.get().strip()
        if t:
            m = parse_hm(t)
            if m is None or not (0 <= m < 24 * 60):
                messagebox.showerror(APP_TITLE, "시간은 13:30 형식으로 입력하세요.", parent=self)
                return None
            t = "%02d:%02d" % divmod(m, 60)
        title = self.e_title.get().strip()
        if not title:
            messagebox.showerror(APP_TITLE, "행사명을 입력하세요.", parent=self)
            return None
        self.date = d
        return {"date": d.isoformat(), "time": t, "title": title, "source": "manual"}

    def _commit(self):
        self.app.store.save()
        self.app.refresh()
        self.reload()

    def add(self):
        ev = self._read_form()
        if ev:
            self.app.store.data["events"].append(ev)
            self.e_title.delete(0, "end")
            self.e_time.delete(0, "end")
            self._commit()

    def update(self):
        sel = self.tree.selection()
        if not sel:
            return
        ev = self._read_form()
        if ev:
            self.app.store.data["events"][int(sel[0])] = ev
            self._commit()

    def delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        del self.app.store.data["events"][int(sel[0])]
        self._commit()


class SettingsDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "설정")
        s, n = app.store.settings, app.store.neis
        nb = ttk.Notebook(self.body)
        nb.pack(fill="both", expand=True)

        # --- 일반 ---
        g = ttk.Frame(nb, padding=12)
        nb.add(g, text="  일반  ")
        self.theme = tk.StringVar(value=s.get("theme", "dark"))
        self.layer = tk.StringVar(value=s.get("layer", "bottom"))
        self.opacity = tk.IntVar(value=int(float(s.get("opacity", 0.93)) * 100))
        self.locked = tk.BooleanVar(value=s.get("locked", False))
        self.startup = tk.BooleanVar(value=startup_enabled())
        self.vis = {k: tk.BooleanVar(value=v) for k, v in s["visible"].items()}

        r = 0
        ttk.Label(g, text="테마").grid(row=r, column=0, sticky="w", pady=4)
        f = ttk.Frame(g)
        f.grid(row=r, column=1, sticky="w")
        ttk.Radiobutton(f, text="다크", variable=self.theme, value="dark").pack(side="left")
        ttk.Radiobutton(f, text="라이트", variable=self.theme, value="light").pack(side="left", padx=8)
        r += 1
        ttk.Label(g, text="불투명도").grid(row=r, column=0, sticky="w", pady=4)
        f = ttk.Frame(g)
        f.grid(row=r, column=1, sticky="w")
        self.op_lbl = ttk.Label(f, text="%d%%" % self.opacity.get(), width=5)
        ttk.Scale(f, from_=30, to=100, variable=self.opacity, length=180,
                  command=self._preview_opacity).pack(side="left")
        self.op_lbl.pack(side="left", padx=6)
        r += 1
        ttk.Label(g, text="창 배치").grid(row=r, column=0, sticky="nw", pady=4)
        f = ttk.Frame(g)
        f.grid(row=r, column=1, sticky="w")
        for val, txt in (("bottom", "바탕화면에 고정 (다른 창 뒤로)"), ("normal", "일반 창"), ("top", "항상 위에 표시")):
            ttk.Radiobutton(f, text=txt, variable=self.layer, value=val).pack(anchor="w")
        r += 1
        ttk.Label(g, text="표시할 위젯").grid(row=r, column=0, sticky="nw", pady=4)
        f = ttk.Frame(g)
        f.grid(row=r, column=1, sticky="w")
        for k, txt in (("calendar", "캘린더"), ("timetable", "수업시간표"), ("events", "오늘 학교행사")):
            ttk.Checkbutton(f, text=txt, variable=self.vis[k]).pack(side="left", padx=(0, 8))
        r += 1
        ttk.Checkbutton(g, text="위젯 위치 잠금 (드래그로 움직이지 않음)", variable=self.locked
                        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=4)
        r += 1
        cb = ttk.Checkbutton(g, text="Windows 시작 시 자동 실행", variable=self.startup)
        cb.grid(row=r, column=0, columnspan=2, sticky="w", pady=4)
        if not IS_WIN:
            cb.state(["disabled"])
        r += 1
        ttk.Label(g, text="데이터 파일: " + app.store.path, foreground="#888"
                  ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # --- NEIS ---
        ne = ttk.Frame(nb, padding=12)
        nb.add(ne, text="  나이스(NEIS) 연동  ")
        ttk.Label(ne, text="교육정보 개방포털(open.neis.go.kr)에서 시간표·학사일정을 불러옵니다.\n"
                           "인증키는 무료로 발급받을 수 있어요. (인증키가 없으면 일부 데이터만 조회됩니다)",
                  justify="left").grid(row=0, column=0, columnspan=4, sticky="w")
        self.v_key = tk.StringVar(value=n.get("api_key", ""))
        self.v_q = tk.StringVar(value=n.get("school_name", ""))
        self.v_grade = tk.StringVar(value=n.get("grade", ""))
        self.v_class = tk.StringVar(value=n.get("class_nm", ""))
        self.v_auto_tt = tk.BooleanVar(value=n.get("auto_timetable", False))
        self.v_auto_ev = tk.BooleanVar(value=n.get("auto_events", False))
        self.school = {k: n.get(k, "") for k in ("office_code", "school_code", "school_name", "school_kind")}

        ttk.Label(ne, text="인증키").grid(row=1, column=0, sticky="w", pady=(10, 4))
        ttk.Entry(ne, textvariable=self.v_key, width=42).grid(row=1, column=1, columnspan=3, sticky="w", pady=(10, 4))
        ttk.Label(ne, text="학교 검색").grid(row=2, column=0, sticky="w", pady=4)
        qe = ttk.Entry(ne, textvariable=self.v_q, width=28)
        qe.grid(row=2, column=1, columnspan=2, sticky="w")
        qe.bind("<Return>", lambda e: self.search())
        ttk.Button(ne, text="검색", command=self.search).grid(row=2, column=3, sticky="w")
        self.results = tk.Listbox(ne, height=5, width=60)
        self.results.grid(row=3, column=0, columnspan=4, sticky="we", pady=4)
        self.results.bind("<<ListboxSelect>>", self.pick_school)
        self.found = []
        self.school_lbl = ttk.Label(ne, text="")
        self.school_lbl.grid(row=4, column=0, columnspan=4, sticky="w")
        self._show_school()
        f = ttk.Frame(ne)
        f.grid(row=5, column=0, columnspan=4, sticky="w", pady=6)
        ttk.Label(f, text="학년").pack(side="left")
        ttk.Entry(f, textvariable=self.v_grade, width=4).pack(side="left", padx=(4, 12))
        ttk.Label(f, text="반").pack(side="left")
        ttk.Entry(f, textvariable=self.v_class, width=4).pack(side="left", padx=4)
        ttk.Checkbutton(ne, text="시작할 때 이번 주 시간표 자동 불러오기 (편집한 시간표를 덮어씁니다)",
                        variable=self.v_auto_tt).grid(row=6, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(ne, text="시작할 때 학사일정(학교행사) 자동 불러오기",
                        variable=self.v_auto_ev).grid(row=7, column=0, columnspan=4, sticky="w")
        f = ttk.Frame(ne)
        f.grid(row=8, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(f, text="시간표 지금 불러오기", command=lambda: self.sync(True, False)).pack(side="left")
        ttk.Button(f, text="학사일정 지금 불러오기", command=lambda: self.sync(False, True)).pack(side="left", padx=4)
        self.neis_status = ttk.Label(ne, text="마지막 동기화: " + (n.get("last_sync") or "없음"), foreground="#888")
        self.neis_status.grid(row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="닫기", command=self.cancel).pack(side="right")
        ttk.Button(bar, text="저장", command=self.save).pack(side="right", padx=4)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.center()

    def cancel(self):
        self.app.apply_settings()  # 불투명도 미리보기 되돌리기
        self.destroy()

    def _preview_opacity(self, _=None):
        v = int(float(self.opacity.get()))
        self.op_lbl.configure(text="%d%%" % v)
        for w in self.app.widgets.values():
            try:
                w.attributes("-alpha", v / 100)
            except tk.TclError:
                pass

    def _show_school(self):
        if self.school.get("school_code"):
            self.school_lbl.configure(text="선택된 학교: %s (%s)" % (self.school["school_name"], self.school["school_kind"]))
        else:
            self.school_lbl.configure(text="선택된 학교: 없음")

    def search(self):
        q = self.v_q.get().strip()
        if not q:
            return
        self.results.delete(0, "end")
        self.results.insert("end", "검색 중...")
        key = self.v_key.get().strip()
        self.app.run_bg(lambda: neis_search_school(q, key), self._on_search)

    def _on_search(self, ok, res):
        if not self.winfo_exists():
            return
        self.results.delete(0, "end")
        if not ok:
            self.results.insert("end", "오류: %s" % res)
            return
        self.found = res
        if not res:
            self.results.insert("end", "검색 결과가 없습니다.")
        for s in res:
            self.results.insert("end", "%s  |  %s  |  %s" % (s["school_name"], s["office_name"], s["address"]))

    def pick_school(self, _=None):
        sel = self.results.curselection()
        if sel and sel[0] < len(self.found):
            s = self.found[sel[0]]
            self.school = {k: s[k] for k in ("office_code", "school_code", "school_name", "school_kind")}
            self._show_school()

    def _apply_neis(self):
        n = self.app.store.neis
        n.update(self.school)
        n["api_key"] = self.v_key.get().strip()
        n["grade"] = self.v_grade.get().strip()
        n["class_nm"] = self.v_class.get().strip()
        n["auto_timetable"] = self.v_auto_tt.get()
        n["auto_events"] = self.v_auto_ev.get()

    def sync(self, tt, ev):
        self._apply_neis()
        self.app.store.save()
        self.neis_status.configure(text="불러오는 중...")

        def done(msg):
            if self.winfo_exists():
                self.neis_status.configure(text=msg)
        self.app.neis_sync(tt, ev, done)

    def save(self):
        s = self.app.store.settings
        theme_changed = s.get("theme") != self.theme.get()
        s["theme"] = self.theme.get()
        s["layer"] = self.layer.get()
        s["opacity"] = round(self.opacity.get() / 100, 2)
        s["locked"] = self.locked.get()
        vis = {k: v.get() for k, v in self.vis.items()}
        if not any(vis.values()):
            messagebox.showwarning(APP_TITLE, "위젯을 최소 1개는 표시해야 해요.", parent=self)
            return
        s["visible"] = vis
        self._apply_neis()
        if IS_WIN and self.startup.get() != startup_enabled():
            try:
                set_startup(self.startup.get())
            except OSError as e:
                messagebox.showerror(APP_TITLE, "자동 실행 설정 실패: %s" % e, parent=self)
        self.app.store.save()
        self.app.rebuild() if theme_changed else self.app.apply_settings()
        self.destroy()


# ----------------------------------------------------------------------------
# 앱
# ----------------------------------------------------------------------------

class App:
    WIDGETS = (("calendar", CalendarWidget), ("timetable", TimetableWidget), ("events", EventsWidget))

    def __init__(self):
        win_dpi_aware()
        self.store = Store()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_TITLE)
        self.fonts = Fonts(self.root)
        try:
            tkfont.nametofont("TkDefaultFont").configure(family=self.fonts.family, size=10)
            tkfont.nametofont("TkTextFont").configure(family=self.fonts.family, size=10)
        except tk.TclError:
            pass
        self.widgets = {}
        self.dialogs = {}
        self._bgq = queue.Queue()
        self._today = dt.date.today()
        self.build()
        self.root.after(1000, self._tick)
        self.root.after(300, self._poll_bg)
        self.root.after(1500, self.auto_sync)

    @property
    def theme(self):
        return THEMES.get(self.store.settings.get("theme"), THEMES["dark"])

    def build(self):
        for key, cls in self.WIDGETS:
            if self.store.settings["visible"].get(key, True):
                self.widgets[key] = cls(self)

    def rebuild(self):
        for w in self.widgets.values():
            w.destroy()
        self.widgets = {}
        self.build()

    def refresh(self):
        for w in self.widgets.values():
            w.render()

    def apply_settings(self):
        vis = self.store.settings["visible"]
        for key, cls in self.WIDGETS:
            if vis.get(key) and key not in self.widgets:
                self.widgets[key] = cls(self)
            elif not vis.get(key) and key in self.widgets:
                self.widgets.pop(key).destroy()
        for w in self.widgets.values():
            w.apply_window_style()

    # --- 주기적 갱신 ---
    def _tick(self):
        now = dt.datetime.now()
        if now.date() != self._today:  # 날짜가 바뀌면 전체 다시 그리기
            self._today = now.date()
            if "calendar" in self.widgets:
                self.widgets["calendar"].go_today()
            self.refresh()
            self.auto_sync()
        for w in self.widgets.values():
            w.tick(now)
        self.root.after(15000, self._tick)

    # --- 메뉴 ---
    def show_menu(self, widget, x, y):
        T = self.theme
        m = tk.Menu(self.root, tearoff=0, bg=T["card"], fg=T["fg"], activebackground=T["accent"],
                    activeforeground=T["accent_fg"], font=self.fonts.normal)
        m.add_command(label="시간표 편집", command=self.open_timetable_editor)
        m.add_command(label="학교행사 · 일정 편집", command=lambda: self.open_event_editor(dt.date.today()))
        n = self.store.neis
        if n.get("school_code"):
            m.add_command(label="나이스에서 불러오기", command=lambda: self.neis_sync(
                bool(n.get("grade")), True, lambda msg: messagebox.showinfo(APP_TITLE, msg)))
        m.add_separator()
        m.add_command(label="설정...", command=self.open_settings)
        m.add_command(label="이 위젯 숨기기", command=lambda: self.hide_widget(widget.key))
        m.add_command(label="위젯 위치 초기화", command=self.reset_positions)
        m.add_command(label="데이터 폴더 열기", command=lambda: open_folder(data_dir()))
        m.add_separator()
        m.add_command(label="종료", command=self.quit)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def hide_widget(self, key):
        vis = self.store.settings["visible"]
        if sum(1 for v in vis.values() if v) <= 1:
            messagebox.showinfo(APP_TITLE, "마지막 위젯은 숨길 수 없어요.\n종료하려면 메뉴의 '종료'를 누르세요.")
            return
        vis[key] = False
        self.store.save()
        self.apply_settings()

    def reset_positions(self):
        self.store.settings["positions"] = {}
        self.store.save()
        for w in self.widgets.values():
            w.geometry("+%d+%d" % w.default_pos)

    def _open(self, name, factory):
        d = self.dialogs.get(name)
        if d is not None and d.winfo_exists():
            d.lift()
            d.focus_force()
            return d
        d = factory()
        self.dialogs[name] = d
        return d

    def open_timetable_editor(self):
        self._open("tt", lambda: TimetableEditor(self))

    def open_event_editor(self, date=None):
        d = self.dialogs.get("ev")
        if d is not None and d.winfo_exists():
            d.destroy()
        self.dialogs["ev"] = EventEditor(self, date)

    def open_settings(self):
        self._open("settings", lambda: SettingsDialog(self))

    def quit(self):
        try:
            self.store.save()
        finally:
            self.root.destroy()

    # --- 백그라운드 작업 (네트워크) ---
    def run_bg(self, func, callback):
        def worker():
            try:
                self._bgq.put((callback, True, func()))
            except Exception as e:  # noqa: BLE001 - 사용자에게 메시지로 보여줌
                self._bgq.put((callback, False, e))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_bg(self):
        try:
            while True:
                cb, ok, res = self._bgq.get_nowait()
                try:
                    cb(ok, res)
                except tk.TclError:
                    pass
        except queue.Empty:
            pass
        self.root.after(300, self._poll_bg)

    # --- NEIS 동기화 ---
    def auto_sync(self):
        n = self.store.neis
        if not n.get("school_code"):
            return
        tt = n.get("auto_timetable") and n.get("grade") and n.get("class_nm")
        ev = n.get("auto_events")
        if (tt or ev) and n.get("last_sync") != dt.date.today().isoformat():
            self.neis_sync(bool(tt), bool(ev), None)

    def neis_sync(self, do_tt, do_ev, on_done):
        neis = dict(self.store.neis)
        today = dt.date.today()

        def job():
            out = {}
            if do_tt:
                out["tt"] = neis_fetch_timetable(neis, today)
            if do_ev:
                out["ev"] = neis_fetch_events(neis, today)
            return out

        def done(ok, res):
            if not ok:
                msg = "불러오기 실패: %s" % res
            else:
                msg = self._apply_neis(res)
                self.store.neis["last_sync"] = today.isoformat()
                self.store.save()
                self.refresh()
            if on_done:
                on_done(msg)
        self.run_bg(job, done)

    def _apply_neis(self, res):
        msgs = []
        if "tt" in res:
            grid, _monday = res["tt"]
            if grid:
                max_p = max(p for _, p in grid)
                periods = self.store.data["periods"]
                while len(periods) < max_p:
                    last = parse_hm(periods[-1]["end"]) if periods else 530
                    periods.append({"name": "%d교시" % (len(periods) + 1),
                                    "start": "%02d:%02d" % divmod(last + 10, 60),
                                    "end": "%02d:%02d" % divmod(last + 55, 60)})
                n = len(periods)
                self.store.data["timetable"] = [[grid.get((d, i + 1), "") for i in range(n)]
                                                for d in range(SCHOOL_DAYS)]
                msgs.append("시간표 %d개 수업" % len(grid))
            else:
                msgs.append("이번 주 시간표 정보가 아직 없어요")
        if "ev" in res:
            events, start, end = res["ev"]
            keep = [e for e in self.store.data["events"]
                    if not (e.get("source") == "neis" and start <= e.get("date", "") <= end)]
            self.store.data["events"] = keep + events
            msgs.append("학사일정 %d건" % len(events))
        return "불러오기 완료: " + ", ".join(msgs)

    def run(self):
        self.root.mainloop()


def single_instance():
    """중복 실행 방지 (로컬 포트 점유)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47821))
        s.listen(1)
        return s
    except OSError:
        return None


def main():
    lock = single_instance()
    if lock is None:
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo(APP_TITLE, "학교 위젯이 이미 실행 중이에요.")
            r.destroy()
        except tk.TclError:
            pass
        return
    App().run()


if __name__ == "__main__":
    main()
