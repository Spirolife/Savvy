#!/usr/bin/env python3
"""
Savvy desktop — tray icon, service supervisor, and chat window.

    python src/desktop.py               # start (or focus) and open the chat window
    python src/desktop.py --background  # start in the tray only (used at login)

The tray app owns the Signal bot and the scheduler: it starts them as child
processes, restarts them if they die, and shows their health in the icon —
green (all good), yellow (running, but Ollama is off so memory search is
recency-only), red (something Savvy needs is down), grey (paused by you).

Closing the chat window only hides it; Savvy keeps running in the tray, like
Discord. "Quit Savvy" in the tray menu stops everything.

Only one copy runs at a time: launching it again just opens the chat window
of the copy that's already running.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import (
    QFileSystemWatcher, QIODeviceBase, QObject, QProcess, QProcessEnvironment, QRectF, QSize, Qt,
    QEvent, QThread, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeyEvent,
    QPainter, QPalette, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextLength, QTextTableFormat,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMenu,
    QPlainTextEdit, QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout,
    QWidget,
)

from paths import CREDENTIALS_DIR, MEMORY_DIR, PROJECT_ROOT, SCHEDULER_HEARTBEAT_PATH

SRC = PROJECT_ROOT / "src"
LOG_DIR = PROJECT_ROOT / "logs"
ICON_SVG = PROJECT_ROOT / "assets" / "savvy.svg"
STATE_PATH = MEMORY_DIR / "desktop_state.json"
INSTANCE_KEY = f"savvy-desktop-{os.environ.get('USER') or os.environ.get('USERNAME') or 'user'}"
IS_WINDOWS = os.name == "nt"

COLORS = {"ok": "#2ecc71", "degraded": "#f1c40f", "down": "#e74c3c", "paused": "#95a5a6"}


# ===========================================================================
# Small persistent state (autostart initialized, etc.)
# ===========================================================================
def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ===========================================================================
# Start on login + app-menu entry
# ===========================================================================
def gui_python() -> str:
    """The interpreter to relaunch with: pythonw on Windows (no console window)."""
    exe = Path(os.path.abspath(sys.executable))   # abspath, not resolve(): keep the venv symlink
    if IS_WINDOWS:
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def launch_command(background: bool) -> list[str]:
    cmd = [gui_python(), str(SRC / "desktop.py")]
    return cmd + ["--background"] if background else cmd


def _desktop_entry(background: bool) -> str:
    exec_line = " ".join(f'"{c}"' for c in launch_command(background))
    return (
        "[Desktop Entry]\nType=Application\nName=Savvy\n"
        "Comment=Private secretary — runs in the system tray\n"
        f"Exec={exec_line}\nIcon={ICON_SVG}\nTerminal=false\n"
        "Categories=Office;Utility;\nStartupNotify=false\n"
        + ("X-GNOME-Autostart-enabled=true\n" if background else "")
    )


class Autostart:
    """Per-OS 'run at login'. Linux: XDG autostart; Windows: HKCU Run key; macOS: LaunchAgent."""

    SYSTEM = platform.system()

    @classmethod
    def _xdg(cls) -> Path:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "autostart" / "savvy.desktop"

    @classmethod
    def _plist(cls) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / "com.savvy.desktop.plist"

    @classmethod
    def enabled(cls) -> bool:
        if cls.SYSTEM == "Windows":
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                    winreg.QueryValueEx(k, "Savvy")
                return True
            except OSError:
                return False
        if cls.SYSTEM == "Darwin":
            return cls._plist().exists()
        return cls._xdg().exists()

    @classmethod
    def set(cls, on: bool) -> None:
        if cls.SYSTEM == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as k:
                if on:
                    cmd = subprocess.list2cmdline(launch_command(background=True))
                    winreg.SetValueEx(k, "Savvy", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(k, "Savvy")
                    except OSError:
                        pass
            return
        if cls.SYSTEM == "Darwin":
            p = cls._plist()
            if on:
                args = "".join(f"<string>{escape(a)}</string>" for a in launch_command(True))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>'
                    '<key>Label</key><string>com.savvy.desktop</string>'
                    f'<key>ProgramArguments</key><array>{args}</array>'
                    '<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
            else:
                p.unlink(missing_ok=True)
            return
        p = cls._xdg()
        if on:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_desktop_entry(background=True), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)


def install_app_menu_entry() -> None:
    """Linux: make Savvy show up in the application launcher. Idempotent."""
    if platform.system() != "Linux":
        return
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    entry = base / "applications" / "savvy.desktop"
    text = _desktop_entry(background=False)
    try:
        if not entry.exists() or entry.read_text(encoding="utf-8") != text:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(text, encoding="utf-8")
    except OSError:
        pass


# ===========================================================================
# Service supervision
# ===========================================================================
def stop_stray_services() -> None:
    """Stop a bot/scheduler started outside the tray (launch.sh), so there's one of each."""
    script = PROJECT_ROOT / "setup" / ("stop.ps1" if IS_WINDOWS else "stop.sh")
    cmd = (["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
           if IS_WINDOWS else ["bash", str(script)])
    try:
        subprocess.run(cmd, capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def start_ollama_if_needed() -> None:
    """Start `ollama serve` if it's installed but not answering (launch.sh did this too)."""
    import shutil
    import httpx
    try:
        httpx.get("http://localhost:11434/api/tags", timeout=1)
        return
    except httpx.HTTPError:
        pass
    exe = shutil.which("ollama")
    if not exe:
        return
    LOG_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / "ollama.log", "a", encoding="utf-8")
    kw = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS}
          if IS_WINDOWS else {"start_new_session": True})
    subprocess.Popen([exe, "serve"], stdout=log, stderr=log, stdin=subprocess.DEVNULL, **kw)


class SigninChecker(QObject):
    """Finds Google accounts that need signing in again, off the UI thread."""

    result = Signal(dict)       # {label: sign-in link}

    def __init__(self):
        super().__init__()
        self._busy = threading.Lock()
        self._again = False

    def check_async(self) -> None:
        self._again = True      # a check already running may have read the old login
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self) -> None:
        if not self._busy.acquire(blocking=False):
            return
        try:
            from google_auth import (get_all_credentials, signin_link, signin_needed,
                                     start_signin_listener)
            start_signin_listener()     # so links work even while services are paused
            while self._again:
                self._again = False
                get_all_credentials()
                self.result.emit({label: signin_link(label) for label in signin_needed()})
        except Exception as e:
            print(f"[desktop] Google sign-in check failed: {e}")
        finally:
            self._busy.release()


# ---- memory watch -------------------------------------------------------------
MEMORY_CHECK_MS = 10 * 60 * 1000    # how often service memory is logged and checked
MEMORY_LIMIT_MB = 800               # default; config "service_memory_limit_mb", 0 turns it off
MEMORY_LOG = LOG_DIR / "memory.log"
HEARTBEAT_STALE = 20 * 60          # seconds without a heartbeat before a service counts as stuck


def process_memory_mb(pid: int) -> float | None:
    """Resident memory of a process in MB, or None when it can't be read."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as f:    # Linux
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    try:                                                            # elsewhere, if installed
        import psutil
        return psutil.Process(pid).memory_info().rss / 1024 / 1024
    except Exception:
        return None


def memory_limit_mb() -> int:
    try:
        with open(CREDENTIALS_DIR / "config.json", encoding="utf-8") as f:
            return int(json.load(f).get("service_memory_limit_mb", MEMORY_LIMIT_MB))
    except (OSError, ValueError, TypeError):
        return MEMORY_LIMIT_MB


def log_memory(line: str) -> None:
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(MEMORY_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M}  {line}\n")
    except OSError:
        pass


class Service(QObject):
    """One supervised child process, restarted with backoff if it exits on its own."""

    changed = Signal()
    died = Signal(str)          # label — emitted on an unexpected exit

    BACKOFF = [5, 15, 30, 60]   # seconds before restart attempt 1, 2, 3, 4+
    STABLE_AFTER = 120          # an attempt that ran this long resets the backoff

    def __init__(self, label: str, args: list[str], log_name: str, parent=None, heartbeat: Path | None = None):
        super().__init__(parent)
        self.label, self.args, self.log_path = label, args, LOG_DIR / log_name
        self.heartbeat = heartbeat     # a file the service touches every loop, if it has one
        self.heartbeat_stale = False   # stale at the last check (see TrayApp.check_heartbeats)
        self.proc: QProcess | None = None
        self.wanted = False
        self.started_at = 0.0
        self.failures = 0
        self.retry = QTimer(self, singleShot=True)
        self.retry.timeout.connect(self._spawn)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    @property
    def restarting(self) -> bool:
        return self.retry.isActive()

    def start(self) -> None:
        self.wanted = True
        self.failures = 0
        if not self.running:
            self._spawn()

    def stop(self) -> None:
        self.wanted = False
        self.retry.stop()
        if self.running:
            # Windows has no SIGTERM for console-less processes; kill outright.
            self.proc.kill() if IS_WINDOWS else self.proc.terminate()
            if not self.proc.waitForFinished(5000):
                self.proc.kill()
                self.proc.waitForFinished(2000)
        self.changed.emit()

    def _spawn(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== started by Savvy desktop {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        p = QProcess(self)
        p.setProgram(sys.executable)
        p.setArguments(["-u", *self.args])
        p.setWorkingDirectory(str(SRC))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        # glibc gives each thread its own malloc arena, and freed memory in them
        # is rarely returned, so threaded services (the app server's request
        # pool, the bot) slowly grow over days. Two arenas keep that in check.
        env.insert("MALLOC_ARENA_MAX", "2")
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.setStandardOutputFile(str(self.log_path), QIODeviceBase.OpenModeFlag.Append)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(lambda _e: self.changed.emit())
        self.proc = p
        self.started_at = time.time()
        p.start()
        self.changed.emit()

    def _finished(self, code: int, _status) -> None:
        if self.wanted:
            if time.time() - self.started_at > self.STABLE_AFTER:
                self.failures = 0
            delay = self.BACKOFF[min(self.failures, len(self.BACKOFF) - 1)]
            self.failures += 1
            self.died.emit(f"{self.label} stopped (exit {code}) — restarting in {delay}s")
            self.retry.start(delay * 1000)
        self.changed.emit()

    def memory_mb(self) -> float | None:
        """Memory the running process holds (resident set), in MB; None if unknown."""
        if not self.running:
            return None
        return process_memory_mb(int(self.proc.processId()))

    def describe(self) -> str:
        if self.running:
            mins = int((time.time() - self.started_at) // 60)
            up = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
            mb = self.memory_mb()
            return f"running · {up}" + (f" · {mb:.0f} MB" if mb else "")
        if self.restarting:
            return "crashed · restarting"
        return "stopped"


def start_signal_container() -> bool:
    """Start the signal-api container if a runtime has it. True if a start succeeded.

    Rootless Podman has no daemon, so the container's restart policy does
    nothing after a reboot; without this the bot and scheduler come up to a
    dead Signal API every time you log in.
    """
    import shutil
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for rt in ("podman", "docker"):
        if not shutil.which(rt):
            continue
        try:
            if rt == "podman" and platform.system() != "Linux":
                subprocess.run([rt, "machine", "start"], capture_output=True, timeout=120, creationflags=flags)
            r = subprocess.run([rt, "start", "signal-api"], capture_output=True, timeout=60, creationflags=flags)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


class HealthChecker(QObject):
    """Polls Ollama and the Signal container off the UI thread, starting the container if it's down."""

    result = Signal(dict)
    START_EVERY = 120           # seconds between container start attempts

    def __init__(self):
        super().__init__()
        self._last_start = 0.0
        self._busy = threading.Lock()

    def check_async(self) -> None:
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self) -> None:
        if not self._busy.acquire(blocking=False):
            return                  # a start attempt is still in progress
        try:
            out = self._probe()
            if not out["signal_api"] and time.time() - self._last_start > self.START_EVERY:
                self._last_start = time.time()
                out["signal_starting"] = True
                self.result.emit(out)
                if start_signal_container():
                    for _ in range(30):         # the API takes a few seconds to answer
                        time.sleep(1)
                        out = self._probe()
                        if out["signal_api"]:
                            break
            self.result.emit(out)
        finally:
            self._busy.release()

    def _probe(self) -> dict:
        import httpx
        from notifier import load_config
        cfg = load_config()
        url = cfg.get("signal_api_url", "http://localhost:8080").rstrip("/")
        out = {"ollama": False, "signal_api": False, "signal_account": False,
               "signal_configured": bool(cfg.get("sender_number"))}
        try:
            out["ollama"] = httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
        except httpx.HTTPError:
            pass
        try:
            httpx.get(f"{url}/v1/about", timeout=2).raise_for_status()
            out["signal_api"] = True
            accounts = httpx.get(f"{url}/v1/accounts", timeout=3).json() or []
            out["signal_account"] = cfg.get("sender_number") in accounts
        except (httpx.HTTPError, ValueError):
            pass
        return out


# ===========================================================================
# Icons
# ===========================================================================
def base_pixmap(size: int = 64) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    QSvgRenderer(str(ICON_SVG)).render(p)
    p.end()
    return pm


def status_icon(status: str) -> QIcon:
    pm = base_pixmap(64)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#ffffff"))
    p.setBrush(QColor(COLORS[status]))
    p.drawEllipse(QRectF(38, 38, 24, 24))
    p.end()
    return QIcon(pm)


# ===========================================================================
# Chat
# ===========================================================================
class ChatWorker(QObject):
    """Runs Savvy turns on its own thread; owns the API client and DB connection."""

    chunk = Signal(str)
    tool = Signal(dict)                 # describe_tool_result() + "name"
    done = Signal(str)
    failed = Signal(str)
    history = Signal(list)
    caught_up = Signal(list)
    confirm = Signal(str, str, int)     # tool name, pretty args, level

    def __init__(self):
        super().__init__()
        self.client = self.memory = None
        self._decision = "deny"
        self._decided = threading.Event()

    def _ensure(self) -> None:
        if self.client is None:
            from core import get_client, load_app_config
            from memory import Memory
            from tool_diagnostics import set_prompt_handler
            self.client = get_client(load_app_config())
            self.memory = Memory(session_id="desktop")
            set_prompt_handler(self._ask_user)

    def _ask_user(self, name: str, inp: dict, _call_id: str, level: int) -> str:
        """Called on this thread by the tool gate; blocks until the GUI answers."""
        shown = {k: v for k, v in inp.items() if k != "_importance"}
        self._decided.clear()
        self.confirm.emit(name, json.dumps(shown, indent=2, ensure_ascii=False, default=str), level)
        self._decided.wait()
        return self._decision

    def _tool_event(self, name: str, _inp: dict, result: str) -> None:
        from tool_diagnostics import describe_tool_result
        self.tool.emit({"name": name, **describe_tool_result(name, result)})

    def answer(self, decision: str) -> None:            # GUI thread
        self._decision = decision
        self._decided.set()

    @Slot()
    def load_history(self) -> None:
        try:
            self._ensure()
            self.history.emit(self.memory.retrieve_recent(n=30))
        except Exception as e:
            self.failed.emit(str(e))

    @Slot(float)
    def catch_up(self, after: float) -> None:
        """Messages from the app, Signal and check-ins saved since `after`."""
        try:
            self._ensure()
            self.caught_up.emit(self.memory.retrieve_others_since(after))
        except Exception as e:
            print(f"[desktop] Catching up on other chats failed: {e}")

    @Slot(str)
    def ask(self, text: str) -> None:
        try:
            from core import finish_pasted_signin
            signin_reply = finish_pasted_signin(text, markdown=True)
            if signin_reply:            # holds a one-time code: not for the model or memory
                self.done.emit(signin_reply)
                return
            self._ensure()
            from core import respond
            reply, _, _ = respond(
                self.client, self.memory, text, surface="repl",
                stream_callback=self.chunk.emit,
                tool_event_callback=self._tool_event,
            )
            self.done.emit(reply)
        except Exception as e:
            self.failed.emit(str(e))


class InputBox(QPlainTextEdit):
    """Enter sends; Shift+Enter adds a line."""

    submit = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)


class ConfirmDialog(QDialog):
    def __init__(self, name: str, args: str, level: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Savvy wants to run a tool")
        self.decision = "deny"
        lay = QVBoxLayout(self)
        kind = "an external action" if level >= 2 else "an action it flagged as important"
        lay.addWidget(QLabel(f"<b>{escape(name)}</b> — {kind}. Allow it?"))
        box = QPlainTextEdit(args)
        box.setReadOnly(True)
        box.setMinimumSize(460, 180)
        lay.addWidget(box)
        btns = QDialogButtonBox()
        for label, decision, role in [("Deny", "deny", QDialogButtonBox.ButtonRole.RejectRole),
                                      ("Always allow this session", "always", QDialogButtonBox.ButtonRole.ActionRole),
                                      ("Approve", "approve", QDialogButtonBox.ButtonRole.AcceptRole)]:
            b = btns.addButton(label, role)
            b.clicked.connect(lambda _=False, d=decision: self._pick(d))
        lay.addWidget(btns)

    def _pick(self, decision: str) -> None:
        self.decision = decision
        self.accept()


# Tool-card badge text and accent color per state. Cards are what the app
# actually ran (built from the tool's own result), so they're styled unlike
# anything the model can write: monospace, tinted, with a colored edge.
TOOL_STATES = {
    "read":   ("READ",   "#7f8c8d"),
    "done":   ("DONE",   "#27ae60"),
    "failed": ("FAILED", "#e74c3c"),
    "denied": ("DENIED", "#e67e22"),
}


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Blend b into a by t (0..1) — tints that work in light and dark themes."""
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


class ChatWindow(QWidget):
    send_to_worker = Signal(str)
    want_history = Signal()
    want_catch_up = Signal(float)      # newest saved timestamp already shown

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Savvy")
        self.setWindowIcon(QIcon(base_pixmap(64)))
        self.resize(560, 680)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.signin_banner = QLabel()
        self.signin_banner.setWordWrap(True)
        self.signin_banner.setOpenExternalLinks(True)
        self.signin_banner.setTextFormat(Qt.TextFormat.RichText)
        self.signin_banner.setVisible(False)
        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(placeholder-text);")
        self.input = InputBox()
        self.input.setPlaceholderText("Message Savvy…  (Enter to send, Shift+Enter for a new line)")
        self.input.setFixedHeight(76)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.input.submit.connect(self.send)

        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.signin_banner)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.status)
        lay.addLayout(row)

        self.messages: list[tuple] = []   # ("you"|"savvy"|"note", text) or ("tool", info)
        self.busy = False
        self.seen_ts = 0.0                # newest saved message shown, for catch_up()
        self._render_timer = QTimer(self, singleShot=True, interval=60)
        self._render_timer.timeout.connect(self._render)

        self.thread = QThread(self)
        self.worker = ChatWorker()
        self.worker.moveToThread(self.thread)
        self.send_to_worker.connect(self.worker.ask)
        self.want_history.connect(self.worker.load_history)
        self.want_catch_up.connect(self.worker.catch_up)
        self.worker.caught_up.connect(self._on_caught_up)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.tool.connect(self._on_tool)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.history.connect(self._on_history)
        self.worker.confirm.connect(self._on_confirm)
        self.thread.start()
        self.want_history.emit()

    # ---- rendering -------------------------------------------------------
    def _render(self) -> None:
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 40
        self.view.clear()
        cur = self.view.textCursor()
        pal = self.view.palette()
        base, text = pal.color(QPalette.ColorRole.Base), pal.color(QPalette.ColorRole.Text)
        accent = pal.color(QPalette.ColorRole.Highlight)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        first = True
        prev = None
        for who, payload in self.messages:
            who, _, via = who.partition(":")
            via = {"signal": " · via Signal", "app": " · via app"}.get(via, "")
            if who == "savvy" and not str(payload).strip() and prev == "tool":
                continue            # the "\n" streamed before a tool call
            if not first:
                cur.insertBlock(QTextBlockFormat(), QTextCharFormat())
            first = False
            if who == "you":
                self._label(cur, "You" + via, accent)
                self._card(cur, payload, _mix(base, accent, 0.12), accent)
            elif who == "savvy":
                if prev not in ("savvy", "tool"):
                    self._label(cur, "Savvy" + via, QColor("#2bb3a3"))
                if payload.strip():
                    cur.insertMarkdown(payload.strip())
                else:
                    cur.insertText("…")
            elif who == "tool":
                if prev not in ("savvy", "tool"):
                    self._label(cur, "Savvy", QColor("#2bb3a3"))
                badge, color = TOOL_STATES.get(payload["state"], ("TOOL", "#888888"))
                c = QColor(color)
                line = f"{badge:<6}  {payload['name']}  →  {payload['detail']}"
                if payload.get("warning"):
                    line += f"\n⚠ {payload['warning']}"
                self._card(cur, line, _mix(base, c, 0.10), c, font=mono)
            else:
                fmt = QTextCharFormat()
                fmt.setFontItalic(True)
                fmt.setForeground(_mix(text, base, 0.45))
                cur.insertText(str(payload), fmt)
            prev = who
        self.view.setTextCursor(cur)
        if at_bottom:
            bar.setValue(bar.maximum())

    @staticmethod
    def _label(cur: QTextCursor, name: str, color: QColor) -> None:
        bf = QTextBlockFormat()
        bf.setTopMargin(6)
        cur.setBlockFormat(bf)
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold)
        fmt.setForeground(color)
        cur.insertText(name, fmt)
        cur.insertBlock(QTextBlockFormat(), QTextCharFormat())

    @staticmethod
    def _card(cur: QTextCursor, content: str, bg: QColor, edge: QColor,
              font: QFont | None = None) -> None:
        """A 1x2 table: thin colored edge cell + tinted content cell."""
        tf = QTextTableFormat()
        tf.setBorder(0)
        tf.setCellSpacing(0)
        tf.setCellPadding(6)
        tf.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
        tf.setColumnWidthConstraints([QTextLength(QTextLength.Type.FixedLength, 4),
                                      QTextLength(QTextLength.Type.VariableLength, 0)])
        table = cur.insertTable(1, 2, tf)
        edge_cell = table.cellAt(0, 0).format()
        edge_cell.setBackground(edge)
        table.cellAt(0, 0).setFormat(edge_cell)
        body = table.cellAt(0, 1)
        body_fmt = body.format()
        body_fmt.setBackground(bg)
        body.setFormat(body_fmt)
        c = body.firstCursorPosition()
        fmt = QTextCharFormat()
        if font is not None:
            fmt.setFont(font)
        c.insertText(content, fmt)
        cur.movePosition(QTextCursor.MoveOperation.End)

    def _schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    # ---- actions ----------------------------------------------------------
    def set_signin_links(self, links: dict) -> None:
        from html import escape
        self.signin_banner.setText("⚠ Google needs you to sign in again: " + " · ".join(
            f'<a href="{escape(url, quote=True)}">{escape(label)}</a>' if url
            else f"{escape(label)} (no link: check credentials/credentials.json)"
            for label, url in links.items()))
        self.signin_banner.setVisible(bool(links))

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return
        self.input.clear()
        self.busy = True
        self.send_btn.setEnabled(False)
        self.status.setText("Savvy is thinking…")
        self.messages += [("you", text), ("savvy", "")]
        self._render()
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
        self.send_to_worker.emit(text)

    def _finish(self) -> None:
        self.busy = False
        self.send_btn.setEnabled(True)
        self.status.setText("")
        self._render()
        self.input.setFocus()

    # ---- worker signals -----------------------------------------------------
    @staticmethod
    def _history_entries(rows: list) -> list[tuple]:
        """Saved messages as (who, text), tagged with the surface they came from."""
        out = []
        for r in rows:
            who, text = ("you" if r["role"] == "user" else "savvy"), r["content"]
            for tag, via in (("[Signal] ", "signal"), ("[App] ", "app")):
                if text.startswith(tag):
                    text, who = text[len(tag):], f"{who}:{via}"
                    break
            out.append((who, text))
        return out

    def _note_seen(self, rows: list) -> None:
        for r in rows:
            try:
                self.seen_ts = max(self.seen_ts, float(r["timestamp"]))
            except (KeyError, TypeError, ValueError):
                pass

    def _on_history(self, rows: list) -> None:
        hist = self._history_entries(rows)
        self._note_seen(rows)
        if hist:
            hist.append(("note", "earlier messages above"))
        self.messages = hist + self.messages
        self._render()
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _on_caught_up(self, rows: list) -> None:
        """Messages said elsewhere (the app, Signal, check-ins) since the last look."""
        rows = [r for r in rows if float(r.get("timestamp") or 0) > self.seen_ts]
        if not rows:
            return
        self._note_seen(rows)
        self.messages += self._history_entries(rows)
        self._render()
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def catch_up(self) -> None:
        """Fetch what was said on other surfaces, unless a reply is in progress."""
        if not self.busy:
            self.want_catch_up.emit(self.seen_ts)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.catch_up()

    def _on_chunk(self, text: str) -> None:
        if self.messages[-1][0] != "savvy":     # text resuming after a tool card
            self.messages.append(("savvy", ""))
        who, cur = self.messages[-1]
        self.messages[-1] = (who, cur + text)
        self.status.setText("Savvy is typing…")
        self._schedule_render()

    def _on_tool(self, info: dict) -> None:
        if self.messages and self.messages[-1] == ("savvy", ""):
            self.messages.pop()                 # nothing streamed before the call
        self.messages.append(("tool", info))
        self.status.setText("Savvy is working…")
        self._schedule_render()

    def _on_done(self, reply: str) -> None:
        # The streamed text after the last tool card is the reply; keep earlier
        # narration and cards as they appeared.
        if self.messages[-1][0] == "savvy":
            self.messages[-1] = ("savvy", reply or self.messages[-1][1] or "(done)")
        elif reply:
            self.messages.append(("savvy", reply))
        self._finish()

    def _on_failed(self, err: str) -> None:
        if self.messages and self.messages[-1] == ("savvy", ""):
            self.messages.pop()
        self.messages.append(("note", f"Error: {err}"))
        self._finish()

    def _on_confirm(self, name: str, args: str, level: int) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        dlg = ConfirmDialog(name, args, level, self)
        dlg.exec()
        self.worker.answer(dlg.decision)
        if dlg.decision == "deny":
            self.status.setText(f"Denied {name}.")

    # ---- window behavior ---------------------------------------------------
    def closeEvent(self, e) -> None:
        e.ignore()          # keep running in the tray
        self.hide()

    def bring_up(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def shutdown(self) -> None:
        self.worker.answer("deny")      # unblock a pending confirmation
        self.thread.quit()
        self.thread.wait(3000)


# ===========================================================================
# Tray
# ===========================================================================
class TrayApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.paused = False
        self.health: dict = {}
        self.chat: ChatWindow | None = None
        self.signin_links: dict = {}
        self.signin_actions: list[QAction] = []

        self.services = [
            Service("Signal bot", ["secretary.py", "--signal"], "signal.log", self),
            Service("Scheduler", ["scheduler.py"], "scheduler.log", self, heartbeat=SCHEDULER_HEARTBEAT_PATH),
            Service("App server", ["app_server.py"], "app_server.log", self),
        ]
        for s in self.services:
            s.changed.connect(self.refresh)
            s.died.connect(lambda msg: self.tray.showMessage(
                "Savvy", msg, QSystemTrayIcon.MessageIcon.Warning, 5000))

        self.checker = HealthChecker()
        self.checker.result.connect(self._on_health)

        self.tray = QSystemTrayIcon(status_icon("paused"), self)
        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self.open_chat)
        self._build_menu()
        self.tray.show()

        self.poll = QTimer(self, interval=15000)
        self.poll.timeout.connect(self.checker.check_async)
        self.poll.timeout.connect(self.refresh)        # keeps uptimes fresh
        self.poll.start()

        self.memory_watch = QTimer(self, interval=MEMORY_CHECK_MS)
        self.memory_watch.timeout.connect(self.check_memory)
        self.memory_watch.timeout.connect(self.check_heartbeats)
        self.memory_watch.start()

        self.signin_checker = SigninChecker()
        self.signin_checker.result.connect(self._on_signin_links)
        self.signin_poll = QTimer(self, interval=60000)  # each check calls Google
        self.signin_poll.timeout.connect(self.signin_checker.check_async)
        self.signin_poll.start()
        self.signin_checker.check_async()
        # A finished sign-in should clear the warning now, not at the next poll.
        # Watching the folder because saving a login replaces the token file.
        self.token_watcher = QFileSystemWatcher([str(CREDENTIALS_DIR)], self)
        self.token_watcher.directoryChanged.connect(lambda _: self.signin_checker.check_async())

        stop_stray_services()
        start_ollama_if_needed()
        self.start_services()
        self.checker.check_async()

    # ---- menu ----------------------------------------------------------------
    def _build_menu(self) -> None:
        m = QMenu()
        self.header = m.addAction("Savvy")
        self.header.setEnabled(False)
        self.lines = {k: m.addAction("") for k in ("bot", "scheduler", "signal", "ollama")}
        for a in self.lines.values():
            a.setEnabled(False)
        m.addSeparator()
        self.open_chat_action = m.addAction("Open chat", self.open_chat)
        self.toggle = m.addAction("Pause services", self.toggle_services)
        m.addAction("Restart services", self.restart_services)
        m.addAction("Pair phone…", self.pair_phone)
        m.addAction("Open logs folder",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR))))
        m.addSeparator()
        self.autostart = QAction("Start on login", m, checkable=True)
        self.autostart.setChecked(Autostart.enabled())
        self.autostart.toggled.connect(Autostart.set)
        m.addAction(self.autostart)
        m.addAction("Quit Savvy", self.quit)
        self.menu = m
        self.tray.setContextMenu(m)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.chat and self.chat.isVisible() and self.chat.isActiveWindow():
                self.chat.hide()
            else:
                self.open_chat()

    # ---- state ---------------------------------------------------------------
    def overall(self) -> tuple[str, str]:
        h = self.health
        bot, sched = self.services[:2]
        if self.paused:
            return "paused", "Paused"
        if h and not h.get("signal_configured"):
            return "down", "Signal isn't set up — run setup/configure.py"
        if not (bot.running and sched.running):
            return "down", "A service has stopped"
        if h and not h.get("signal_api"):
            return "down", "Signal container is down"
        if h and not h.get("signal_account"):
            return "down", "Signal number isn't registered in the container"
        if self.signin_links:
            return "degraded", "Google sign-in needed: " + ", ".join(self.signin_links)
        if h and not h.get("ollama"):
            return "degraded", "Running · Ollama off (recency-only memory)"
        return "ok", "All systems running"

    @Slot()
    def refresh(self) -> None:
        status, summary = self.overall()
        bot, sched = self.services[:2]
        h = self.health
        dot = lambda good: "●" if good else "○"
        self.header.setText(f"Savvy — {summary}")
        self.lines["bot"].setText(f"{dot(bot.running)}  Signal bot: {bot.describe()}")
        self.lines["scheduler"].setText(f"{dot(sched.running)}  Scheduler: {sched.describe()}")
        sig = ("up" if h.get("signal_account") else "up, number not registered"
               if h.get("signal_api") else "starting…" if h.get("signal_starting")
               else "down") if h else "checking…"
        self.lines["signal"].setText(f"{dot(h.get('signal_account'))}  Signal container: {sig}")
        oll = ("running" if h.get("ollama") else "off") if h else "checking…"
        self.lines["ollama"].setText(f"{dot(h.get('ollama'))}  Ollama (memory search): {oll}")
        self.toggle.setText("Start services" if self.paused else "Pause services")
        self.tray.setIcon(status_icon(status))
        self.tray.setToolTip(f"Savvy — {summary}")

    def _on_signin_links(self, links: dict) -> None:
        new = set(links) - set(self.signin_links)
        self.signin_links = links
        for action in self.signin_actions:
            self.menu.removeAction(action)
        self.signin_actions = []
        for label, url in links.items():
            if not url:
                continue
            action = QAction(f"Sign in to Google: {label}", self.menu)
            action.triggered.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            self.menu.insertAction(self.open_chat_action, action)
            self.signin_actions.append(action)
        if self.chat:
            self.chat.set_signin_links(links)
        if new:
            self.tray.showMessage(
                "Savvy", f"Google needs you to sign in again: {', '.join(sorted(new))}. "
                "Click here for the link.", QSystemTrayIcon.MessageIcon.Warning, 8000)
        self.refresh()

    def _on_health(self, h: dict) -> None:
        self.health = h
        self.refresh()

    # ---- actions -------------------------------------------------------------
    def start_services(self) -> None:
        self.paused = False
        for s in self.services:
            s.start()
        self.refresh()

    def stop_services(self) -> None:
        for s in self.services:
            s.stop()

    def toggle_services(self) -> None:
        if self.paused:
            self.start_services()
        else:
            self.paused = True
            self.stop_services()
            self.refresh()

    def restart_services(self) -> None:
        self.stop_services()
        self.start_services()

    @Slot()
    def check_memory(self) -> None:
        """Log every service's memory to logs/memory.log; restart one over the limit.

        A long-running service that keeps growing would otherwise take gigabytes
        over a few days. The log shows which one grows and how fast.
        """
        readings = [(s, s.memory_mb()) for s in self.services]
        tray_mb = process_memory_mb(os.getpid())
        parts = [f"{s.label} {mb:.0f}" for s, mb in readings if mb] + ([f"Tray {tray_mb:.0f}"] if tray_mb else [])
        if parts:
            log_memory("MB  " + "  ".join(parts))
        limit = memory_limit_mb()
        if self.paused or limit <= 0:
            return
        for s, mb in readings:
            if mb and mb > limit:
                msg = f"{s.label} was using {mb:.0f} MB (limit {limit} MB), so Savvy restarted it."
                log_memory(msg)
                print(f"[desktop] {msg}")
                s.stop()
                s.start()
                self.tray.showMessage("Savvy", msg, QSystemTrayIcon.MessageIcon.Information, 8000)

    @Slot()
    def check_heartbeats(self) -> None:
        """Restart a service whose loop stopped beating (stuck, not crashed).

        Only once it has beaten since it started: one still starting up (waiting
        for the Signal container, say) hasn't reached its loop yet. And only when
        it was already stale at the previous check: right after the PC wakes from
        sleep every heartbeat looks old, but a live loop beats again within minutes.
        """
        if self.paused:
            return
        now = time.time()
        for s in self.services:
            if s.heartbeat is None or not s.running:
                continue
            try:
                last = s.heartbeat.stat().st_mtime
            except OSError:
                continue
            stale = last >= s.started_at and now - last > HEARTBEAT_STALE
            was_stale, s.heartbeat_stale = s.heartbeat_stale, stale
            if stale and was_stale:
                s.heartbeat_stale = False
                msg = (f"{s.label} stopped responding (nothing for {(now - last) // 60:.0f} minutes), "
                       "so Savvy restarted it.")
                log_memory(msg)
                print(f"[desktop] {msg}")
                s.stop()
                s.start()
                self.tray.showMessage("Savvy", msg, QSystemTrayIcon.MessageIcon.Warning, 8000)

    def pair_phone(self) -> None:
        """Show a one-time code for the Savvy phone app (the app server checks it)."""
        from PySide6.QtWidgets import QMessageBox
        try:
            from app_api.auth import CODE_TTL, new_pairing_code
            text = (f"Enter this code in the Savvy app:\n\n{new_pairing_code()}\n\n"
                    f"It works once, for {CODE_TTL // 60} minutes.")
        except Exception as e:
            text = f"Couldn't make a pairing code: {e}"
        QMessageBox.information(None, "Pair phone", text)

    def open_chat(self) -> None:
        if self.chat is None:
            self.chat = ChatWindow()
            self.chat.set_signin_links(self.signin_links)
        self.chat.bring_up()

    def quit(self) -> None:
        self.paused = True
        self.stop_services()
        if self.chat:
            self.chat.shutdown()
        self.tray.hide()
        self.app.quit()


# ===========================================================================
# Entry
# ===========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Savvy desktop (tray + chat)")
    parser.add_argument("--background", action="store_true", help="start in the tray without opening the chat")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("Savvy")
    app.setDesktopFileName("savvy")
    app.setQuitOnLastWindowClosed(False)

    # Already running? Ask that copy to show its window and leave.
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(500):
        sock.write(b"show" if not args.background else b"noop")
        sock.waitForBytesWritten(500)
        return 0
    QLocalServer.removeServer(INSTANCE_KEY)         # stale socket from a crash
    server = QLocalServer()
    server.listen(INSTANCE_KEY)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[desktop] No system tray found — the chat window will be the only UI.")

    install_app_menu_entry()
    state = load_state()
    if not state.get("autostart_initialized"):
        try:
            Autostart.set(True)
        except OSError as e:
            print(f"[desktop] Could not enable start on login: {e}")
        state["autostart_initialized"] = True
        save_state(state)

    tray = TrayApp(app)

    def on_connection():
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(500)
        if bytes(conn.readAll()) == b"show":
            tray.open_chat()
        conn.close()
    server.newConnection.connect(on_connection)

    if not args.background:
        tray.open_chat()
    app.aboutToQuit.connect(lambda: [s.stop() for s in tray.services])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
