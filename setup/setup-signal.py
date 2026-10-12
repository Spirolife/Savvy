#!/usr/bin/env python3
"""
setup-signal.py — start the signal-cli-rest-api container Savvy talks to.

Run:  python setup/setup-signal.py     (stdlib only; no venv needed)

Works on Linux, macOS and Windows with Podman or Docker, whichever is
installed (Podman preferred: rootless, no daemon). If neither is present it
offers to install Podman with the system package manager. On macOS/Windows
Podman runs inside a small VM ("podman machine"), which this starts for you.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

NAME = "signal-api"
IMAGE = "docker.io/bbernhard/signal-cli-rest-api"
PORT = 8080
API = f"http://localhost:{PORT}"
SYSTEM = platform.system()          # "Linux", "Darwin", "Windows"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, **kw)


def quiet_ok(cmd: list[str]) -> bool:
    try:
        return run(cmd, capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ask_yes(prompt: str) -> bool:
    return not input(f"{prompt} [Y/n] ").strip().lower().startswith("n")


def api_up() -> bool:
    try:
        urllib.request.urlopen(f"{API}/v1/about", timeout=2)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 1. Container runtime
# ---------------------------------------------------------------------------
def install_command() -> list[str] | None:
    if SYSTEM == "Windows" and shutil.which("winget"):
        return ["winget", "install", "-e", "--id", "RedHat.Podman"]
    if SYSTEM == "Darwin" and shutil.which("brew"):
        return ["brew", "install", "podman"]
    for mgr, cmd in [("apt-get", ["sudo", "apt-get", "install", "-y", "podman"]),
                     ("dnf", ["sudo", "dnf", "install", "-y", "podman"]),
                     ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "podman"]),
                     ("zypper", ["sudo", "zypper", "install", "-y", "podman"])]:
        if shutil.which(mgr):
            return cmd
    return None


def install_runtime() -> None:
    if shutil.which("rpm-ostree"):
        sys.exit("[!] Immutable system: run 'rpm-ostree install podman', reboot, then re-run this.")
    cmd = install_command()
    if not cmd:
        sys.exit("[!] Install Podman (podman.io) or Docker, then re-run this.")
    if not ask_yes(f"Neither Podman nor Docker found. Install Podman now ({' '.join(cmd)})?"):
        sys.exit("[!] Skipped. Install Podman or Docker, then re-run this.")
    if run(cmd).returncode != 0:
        sys.exit("[✗] Install failed — see the output above.")
    if not shutil.which("podman"):
        # winget/brew update PATH for new shells only.
        sys.exit("[✓] Podman installed. Open a NEW terminal and re-run this to continue.")


def ensure_podman_machine() -> None:
    """macOS/Windows: podman needs its Linux VM running."""
    if quiet_ok(["podman", "info"]):
        return
    machines = run(["podman", "machine", "list", "--format", "json"],
                   capture_output=True).stdout or "[]"
    if not json.loads(machines):
        print("[•] Creating the Podman VM (one-time, a few minutes)...")
        if run(["podman", "machine", "init"]).returncode != 0:
            sys.exit("[✗] 'podman machine init' failed. On Windows this needs WSL: "
                     "run 'wsl --install' as admin, reboot, and re-run.")
    print("[•] Starting the Podman VM...")
    run(["podman", "machine", "start"])
    if not quiet_ok(["podman", "info"]):
        sys.exit("[✗] Podman still isn't reachable. Try 'podman machine start' by hand.")


def pick_runtime() -> list[str]:
    if not (shutil.which("podman") or shutil.which("docker")):
        install_runtime()

    if shutil.which("podman"):
        if SYSTEM != "Linux":
            ensure_podman_machine()
        return ["podman"]

    if quiet_ok(["docker", "info"]):
        return ["docker"]
    if SYSTEM == "Linux" and shutil.which("sudo") and quiet_ok(["sudo", "docker", "info"]):
        print("[•] Your user can't talk to the Docker daemon; using sudo for this run.")
        print("    To avoid that next time:  sudo usermod -aG docker $USER  (then log out/in)")
        return ["sudo", "docker"]
    if SYSTEM == "Linux":
        sys.exit("[!] Can't reach the Docker daemon. Is it running? (sudo systemctl start docker)")
    sys.exit("[!] Docker isn't running. Start Docker Desktop, wait for it to finish, then re-run.")


# ---------------------------------------------------------------------------
# 2. Container
# ---------------------------------------------------------------------------
def names(rt: list[str], all_: bool) -> list[str]:
    cmd = rt + ["ps", "--format", "{{.Names}}"] + (["-a"] if all_ else [])
    return (run(cmd, capture_output=True).stdout or "").split()


def start_container(rt: list[str]) -> None:
    if NAME in names(rt, all_=False):
        print(f"[✓] {NAME} container is already running")
        return
    if NAME in names(rt, all_=True):
        print(f"[•] Starting existing {NAME} container...")
        run(rt + ["start", NAME], check=True)
        return
    print("[•] Pulling and starting signal-cli-rest-api (first run downloads ~400 MB)...")
    # Bound to localhost: anyone who can reach this port can send Signal
    # messages as you. --restart is honored by Docker's daemon; Podman users
    # get a systemd unit suggestion below.
    run(rt + ["run", "-d", "--name", NAME,
              "--restart", "unless-stopped",
              "-p", f"127.0.0.1:{PORT}:8080",
              "-v", "signal-cli-data:/home/.local/share/signal-cli",
              "-e", "MODE=native",
              IMAGE], check=True)


def main() -> int:
    print("=" * 41 + "\n  Signal Container Setup\n" + "=" * 41)
    rt = pick_runtime()
    version = run(rt + ["--version"], capture_output=True).stdout.strip()
    print(f"[✓] Using {version}")

    try:
        start_container(rt)
    except subprocess.CalledProcessError:
        print(f"[✗] Couldn't start the container. Check:  {' '.join(rt)} logs {NAME}")
        return 1

    print("[•] Waiting for the API...")
    for _ in range(30):
        if api_up():
            break
        time.sleep(1)
    else:
        print(f"[!] API not responding after 30s. Check:  {' '.join(rt)} logs {NAME}")
        return 1
    print(f"[✓] Signal API is responding on {API}")

    print("\nLink your phone (setup/configure.py walks you through this too):")
    print(f"  1. Open {API}/v1/qrcodelink?device_name=savvy")
    print("  2. Phone: Signal → Settings → Linked devices → Link new device → scan")
    if rt[-1] == "podman" and SYSTEM == "Linux":
        print("\nOptional — start the container on login:")
        print("  mkdir -p ~/.config/systemd/user")
        print(f"  podman generate systemd --name {NAME} --new > ~/.config/systemd/user/container-{NAME}.service")
        print(f"  systemctl --user daemon-reload && systemctl --user enable container-{NAME}.service")
    elif rt[-1] == "podman":
        print("\nAfter a reboot, start it again with:  podman machine start; podman start signal-api")
    else:
        print("\nDocker restarts the container on boot automatically (--restart unless-stopped).")
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        os.system("")   # ANSI/Unicode-friendly console
    sys.exit(main())
