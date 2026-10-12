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
    QIODeviceBase, QObject, QProcess, QProcessEnvironment, QRectF, QSize, Qt,
    QThread, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QKeyEvent, QPainter, QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMenu,
    QPlainTextEdit, QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout,
    QWidget,
)

from paths import MEMORY_DIR, PROJECT_ROOT

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


class Service(QObject):
    """One supervised child process, restarted with backoff if it exits on its own."""

    changed = Signal()
    died = Signal(str)          # label — emitted on an unexpected exit

    BACKOFF = [5, 15, 30, 60]   # seconds before restart attempt 1, 2, 3, 4+
    STABLE_AFTER = 120          # an attempt that ran this long resets the backoff

    def __init__(self, label: str, args: list[str], log_name: str, parent=None):
        super().__init__(parent)
        self.label, self.args, self.log_path = label, args, LOG_DIR / log_name
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

    def describe(self) -> str:
        if self.running:
            mins = int((time.time() - self.started_at) // 60)
            up = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
            return f"running · {up}"
        if self.restarting:
            return "crashed · restarting"
        return "stopped"


class HealthChecker(QObject):
    """Polls Ollama and the Signal container off the UI thread."""

    result = Signal(dict)

    def check_async(self) -> None:
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self) -> None:
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
        self.result.emit(out)


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
    tool = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    history = Signal(list)
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

    @Slot(str)
    def ask(self, text: str) -> None:
        try:
            self._ensure()
            from core import respond
            reply, _, _ = respond(
                self.client, self.memory, text, surface="repl",
                stream_callback=self.chunk.emit,
                tool_event_callback=lambda name, _inp, _res: self.tool.emit(name),
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


def _md_quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.splitlines())


class ChatWindow(QWidget):
    send_to_worker = Signal(str)
    want_history = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Savvy")
        self.setWindowIcon(QIcon(base_pixmap(64)))
        self.resize(560, 680)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
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
        lay.addWidget(self.view, 1)
        lay.addWidget(self.status)
        lay.addLayout(row)

        self.messages: list[tuple[str, str]] = []   # (who, markdown)
        self.busy = False
        self._render_timer = QTimer(self, singleShot=True, interval=60)
        self._render_timer.timeout.connect(self._render)

        self.thread = QThread(self)
        self.worker = ChatWorker()
        self.worker.moveToThread(self.thread)
        self.send_to_worker.connect(self.worker.ask)
        self.want_history.connect(self.worker.load_history)
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
        parts = []
        for who, text in self.messages:
            if who == "you":
                parts.append(f"**You**\n\n{_md_quote(text)}")
            elif who == "savvy":
                parts.append(f"**Savvy**\n\n{text or '…'}")
            else:
                parts.append(f"*{text}*")
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 40
        self.view.setMarkdown("\n\n---\n\n".join(parts))
        if at_bottom:
            bar.setValue(bar.maximum())

    def _schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    # ---- actions ----------------------------------------------------------
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
    def _on_history(self, rows: list) -> None:
        hist = []
        for r in rows:
            who, text = ("you" if r["role"] == "user" else "savvy"), r["content"]
            if text.startswith("[Signal] "):
                text = text[len("[Signal] "):]
                if who == "you":
                    text += "\n\n*via Signal*"
            hist.append((who, text))
        if hist:
            hist.append(("note", "earlier messages above"))
        self.messages = hist + self.messages
        self._render()
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _on_chunk(self, text: str) -> None:
        who, cur = self.messages[-1]
        self.messages[-1] = (who, cur + text)
        self.status.setText("Savvy is typing…")
        self._schedule_render()

    def _on_tool(self, name: str) -> None:
        self.status.setText(f"Savvy used {name.replace('_', ' ')}…")

    def _on_done(self, reply: str) -> None:
        self.messages[-1] = ("savvy", reply or "(done)")
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

        self.services = [
            Service("Signal bot", ["secretary.py", "--signal"], "signal.log", self),
            Service("Scheduler", ["scheduler.py"], "scheduler.log", self),
        ]
        for s in self.services:
            s.changed.connect(self.refresh)
            s.died.connect(lambda msg: self.tray.showMessage(
                "Savvy", msg, QSystemTrayIcon.MessageIcon.Warning, 5000))

        self.checker = HealthChecker()
        self.checker.result.connect(self._on_health)

        self.tray = QSystemTrayIcon(status_icon("paused"), self)
        self.tray.activated.connect(self._on_activated)
        self._build_menu()
        self.tray.show()

        self.poll = QTimer(self, interval=15000)
        self.poll.timeout.connect(self.checker.check_async)
        self.poll.timeout.connect(self.refresh)        # keeps uptimes fresh
        self.poll.start()

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
        m.addAction("Open chat", self.open_chat)
        self.toggle = m.addAction("Pause services", self.toggle_services)
        m.addAction("Restart services", self.restart_services)
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
        bot, sched = self.services
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
        if h and not h.get("ollama"):
            return "degraded", "Running · Ollama off (recency-only memory)"
        return "ok", "All systems running"

    @Slot()
    def refresh(self) -> None:
        status, summary = self.overall()
        bot, sched = self.services
        h = self.health
        dot = lambda good: "●" if good else "○"
        self.header.setText(f"Savvy — {summary}")
        self.lines["bot"].setText(f"{dot(bot.running)}  Signal bot: {bot.describe()}")
        self.lines["scheduler"].setText(f"{dot(sched.running)}  Scheduler: {sched.describe()}")
        sig = ("up" if h.get("signal_account") else "up, number not registered"
               if h.get("signal_api") else "down") if h else "checking…"
        self.lines["signal"].setText(f"{dot(h.get('signal_account'))}  Signal container: {sig}")
        oll = ("running" if h.get("ollama") else "off") if h else "checking…"
        self.lines["ollama"].setText(f"{dot(h.get('ollama'))}  Ollama (memory search): {oll}")
        self.toggle.setText("Start services" if self.paused else "Pause services")
        self.tray.setIcon(status_icon(status))
        self.tray.setToolTip(f"Savvy — {summary}")

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

    def open_chat(self) -> None:
        if self.chat is None:
            self.chat = ChatWindow()
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
