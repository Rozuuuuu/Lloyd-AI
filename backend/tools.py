"""
JARVIS PC control toolkit.
Every function returns a short human-readable string.
"""
import subprocess, os, webbrowser, platform, time, sys, json, threading
import json as _json
import urllib.request as _ur
import urllib.parse as _uq
import urllib.error as _ue
from datetime import datetime
from pathlib import Path
import re
import subprocess, os, webbrowser, platform, time, sys, json, threading
import json as _json
import urllib.request as _ur
import urllib.error as _ue

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# ---------- lazy pyautogui ----------
_pg = None
def _gui():
    global _pg
    if _pg is None:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.03
        _pg = pyautogui
    return _pg


# ---------- memory bridge ----------
_memory_ref = None

def register_memory(mem):
    global _memory_ref
    _memory_ref = mem


# ---------- speech bridge ----------
_speech_ref = None

def register_speech(ctl):
    global _speech_ref
    _speech_ref = ctl


def _speech():
    """The live SpeechController, imported lazily if nobody registered one.

    server.py registers it at startup. The lazy import means the speech tools
    also work when tools.py is driven directly, as the test harnesses do.
    """
    global _speech_ref
    if _speech_ref is None:
        try:
            from speech import controller
            _speech_ref = controller
        except Exception:
            return None
    return _speech_ref


# ============================================================
#  PATH HELPERS (OneDrive-aware)
# ============================================================

def get_desktop_path() -> str:
    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            val, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            path = os.path.expandvars(val)
            if os.path.isdir(path):
                return path
        except Exception:
            pass
    home = Path.home()
    for c in [home / "Desktop", home / "OneDrive" / "Desktop",
              home / "OneDrive - Personal" / "Desktop"]:
        if c.is_dir():
            return str(c)
    return str(home)


def get_documents_path() -> str:
    home = Path.home()
    for c in [home / "Documents", home / "OneDrive" / "Documents",
              home / "OneDrive - Personal" / "Documents"]:
        if c.is_dir():
            return str(c)
    return str(home)


def get_downloads_path() -> str:
    home = Path.home()
    for c in [home / "Downloads", home / "OneDrive" / "Downloads"]:
        if c.is_dir():
            return str(c)
    return str(home)


# ---------- PROJECT ROOT ----------
# tools.py lives in <project>/backend/, so the project folder is one level up.
# Derived from __file__ so it stays correct if the whole folder is moved.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_project_path() -> str:
    return PROJECT_ROOT


# ---------- PATH ALIAS RESOLUTION ----------
_PATH_ALIASES = {
    "desktop":         get_desktop_path,
    "my desktop":      get_desktop_path,
    "the desktop":     get_desktop_path,
    "documents":       get_documents_path,
    "my documents":    get_documents_path,
    "the documents":   get_documents_path,
    "downloads":       get_downloads_path,
    "my downloads":    get_downloads_path,
    "the downloads":   get_downloads_path,
    "home":            lambda: str(Path.home()),
    "my home":         lambda: str(Path.home()),
    "user folder":     lambda: str(Path.home()),
    "my user folder":  lambda: str(Path.home()),
    # The project itself. Without these, "the Jarvis AI folder" resolved to a
    # bare relative path and landed wherever the backend happened to be started
    # from -- which is what made JARVIS claim the folder did not exist.
    "jarvis":            get_project_path,
    "jarvis ai":         get_project_path,
    "jarvis folder":     get_project_path,
    "jarvis ai folder":  get_project_path,
    "the jarvis folder": get_project_path,
    "my jarvis folder":  get_project_path,
    "project":           get_project_path,
    "the project":       get_project_path,
    "my project":        get_project_path,
    "project folder":    get_project_path,
    "project root":      get_project_path,
    "backend":           lambda: os.path.join(PROJECT_ROOT, "backend"),
    "jarvis backend":    lambda: os.path.join(PROJECT_ROOT, "backend"),
}


def search_roots() -> list:
    """Folders a bare name like "Jarvis AI" is looked for in, in priority order."""
    roots = [PROJECT_ROOT, get_desktop_path(), get_documents_path(),
             get_downloads_path(), str(Path.home())]
    seen, out = set(), []
    for r in roots:
        if r and r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def known_folders() -> dict:
    """Real absolute paths for the folders any AI may be asked about."""
    return {
        "project (Jarvis AI)": PROJECT_ROOT,
        "desktop":             get_desktop_path(),
        "documents":           get_documents_path(),
        "downloads":           get_downloads_path(),
        "home":                str(Path.home()),
    }


def folder_context() -> str:
    """One-line-per-folder map, handed to other AIs so they never have to guess."""
    return "\n".join("{}: {}".format(k, v) for k, v in known_folders().items())


def _squash(s: str) -> str:
    """Reduce a name to letters and digits, lowercased.

    The model writes "JarvisAI", "jarvis-ai" and "Jarvis_AI" interchangeably, so
    alias matching cannot depend on spacing or punctuation.
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


# Built once: squashed alias key -> resolver.
_SQUASHED_ALIASES = {_squash(k): v for k, v in _PATH_ALIASES.items()}


def _alias_lookup(key: str):
    if key in _PATH_ALIASES:
        return _PATH_ALIASES[key]()
    clean = key.replace(" folder", "").replace(" directory", "").strip()
    if clean in _PATH_ALIASES:
        return _PATH_ALIASES[clean]()

    # Spacing-insensitive fallback: "JarvisAI", "jarvis-ai", "Jarvis_AI".
    for candidate in (key, clean):
        sq = _squash(candidate)
        if sq in _SQUASHED_ALIASES:
            return _SQUASHED_ALIASES[sq]()
        for suffix in ("folder", "directory"):
            if sq.endswith(suffix):
                trimmed = sq[:-len(suffix)]
                if trimmed in _SQUASHED_ALIASES:
                    return _SQUASHED_ALIASES[trimmed]()
    return None


def _find_in_roots(name: str):
    """Look for a bare name in each search root, case-insensitively."""
    target = name.strip().strip("/\\")
    if not target or os.sep in target or "/" in target:
        return None
    low = target.lower()
    for root in search_roots():
        direct = os.path.join(root, target)
        if os.path.exists(direct):
            return direct
        try:
            for entry in os.listdir(root):
                if entry.lower() == low:
                    return os.path.join(root, entry)
        except (PermissionError, OSError):
            continue
    return None


def resolve_path(p: str) -> str:
    """Turn 'my desktop' or 'Jarvis AI' into a real absolute Windows path.

    Always returns an ABSOLUTE path. The old version could return a bare
    relative string, which then resolved against whatever directory uvicorn was
    started from -- so "the Jarvis AI folder" silently pointed at nothing and
    the model invented an explanation for the failure.
    """
    if p is None:
        return str(Path.home())
    s = str(p).strip().strip('"').strip("'")
    if s in ("", ".", "~", "~/"):
        return str(Path.home())

    key = s.lower().rstrip("/\\").strip()
    hit = _alias_lookup(key)
    if hit:
        return hit

    expanded = os.path.expanduser(os.path.expandvars(s))
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)

    # Compound alias path, e.g. "my desktop/AI/Jarvis AI" or "documents\\notes".
    # The model composes these freely, and without this they resolved to nothing.
    parts = [seg for seg in re.split(r"[\\/]+", s.strip()) if seg]
    if len(parts) > 1:
        base = _alias_lookup(parts[0].strip().lower())
        if base:
            joined = os.path.join(base, *parts[1:])
            if os.path.exists(joined):
                return os.path.normpath(joined)
        # Or the whole thing squashed: "myprojects/JarvisAI" -> the project.
        whole = _alias_lookup(" ".join(parts).lower())
        if whole:
            return os.path.normpath(whole)
        # Last resort: search for just the final segment.
        tail = _find_in_roots(parts[-1])
        if tail:
            return os.path.normpath(tail)

    # Bare name: look for it in the project, Desktop, Documents, Downloads, home.
    found = _find_in_roots(s)
    if found:
        return os.path.normpath(found)

    # Still unknown. Anchor to the project root rather than the process cwd, so
    # the path in any error message is stable and truthful.
    return os.path.normpath(os.path.join(PROJECT_ROOT, expanded))


def path_not_found(original: str) -> str:
    """Error text that names where we actually looked.

    The model fabricated "in your documents directory" precisely because the old
    error gave it no location to report. Give it the real list.
    """
    roots = ", ".join(search_roots())
    return ("No file or folder named '{}' was found. Looked in: {}. "
            "Ask for the full path if you need somewhere else."
            .format(original, roots))


# ============================================================
#  FILES & SHELL
# ============================================================

BLOCKED = ["rm -rf /", "mkfs", "dd if=", ":(){", "> /dev/sda",
           "format c:", "del /f /s /q c:", "rmdir /s /q c:"]


def run_shell_command(command: str) -> str:
    for b in BLOCKED:
        if b.lower() in command.lower():
            return f"BLOCKED: '{command}' is destructive."
    try:
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=45)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if out:
            return out[:4000]
        if err:
            return f"(stderr) {err[:4000]}"
        return f"(no output) exit={r.returncode}"
    except subprocess.TimeoutExpired:
        return "Error: command timed out."
    except Exception as e:
        return f"Error: {e}"


def read_file(path: str) -> str:
    try:
        full = resolve_path(path)
        if not os.path.exists(full):
            return path_not_found(path)
        if os.path.isdir(full):
            return (f"'{full}' is a folder, not a file. "
                    f"Use list_dir to see what is inside.")
        with open(full, encoding="utf-8", errors="replace") as f:
            data = f.read()
        return f"Contents of {full}:\n{data[:5000]}"
    except Exception as e:
        return f"Error reading {path}: {e}"


def write_file(path: str, content: str) -> str:
    try:
        full = resolve_path(path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} characters to {full}"
    except Exception as e:
        return f"Error writing {path}: {e}"


def list_dir(path: str = ".") -> str:
    try:
        full = resolve_path(path)
        if not os.path.isdir(full):
            if os.path.isfile(full):
                return (f"'{full}' is a file, not a folder. "
                        f"Use read_file to read it.")
            return path_not_found(path)
        entries = sorted(os.listdir(full))
        if not entries:
            return f"{full} is empty."
        return "\n".join(entries)[:4000]
    except Exception as e:
        return f"Error listing {path}: {e}"


def create_folder(path: str) -> str:
    try:
        full = resolve_path(path)
        os.makedirs(full, exist_ok=True)
        return f"Created folder: {full}"
    except Exception as e:
        return f"Error: {e}"


def move_path(src: str, dst: str) -> str:
    import shutil
    try:
        s, d = resolve_path(src), resolve_path(dst)
        shutil.move(s, d)
        return f"Moved {s} -> {d}"
    except Exception as e:
        return f"Error: {e}"


def copy_path(src: str, dst: str) -> str:
    import shutil
    try:
        s, d = resolve_path(src), resolve_path(dst)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        return f"Copied {s} -> {d}"
    except Exception as e:
        return f"Error: {e}"


def delete_path(path: str, **_ignored) -> str:
    full = resolve_path(path)
    if not os.path.exists(full):
        return path_not_found(path)
    go, msg = _confirm_gate("delete:" + os.path.normpath(full).lower(),
                            "move %s to the Recycle Bin" % full,
                            "delete_path", {"path": path})
    if not go:
        return msg
    try:
        from send2trash import send2trash
        send2trash(full)
        return f"Sent to Recycle Bin: {full}"
    except Exception as e:
        return f"Error: {e}"


def find_files(pattern: str, search_root: str = "") -> str:
    try:
        root = resolve_path(search_root) if search_root else str(Path.home())
        matches = []
        for i, p in enumerate(Path(root).rglob(pattern)):
            matches.append(str(p))
            if i > 200:
                break
        if not matches:
            return f"No files matching '{pattern}' under {root}"
        return f"Found {len(matches)} match(es):\n" + "\n".join(matches[:100])
    except Exception as e:
        return f"Error: {e}"


def grep_files(pattern: str, search_root: str = "", max_results: int = 50) -> str:
    import re as _re
    try:
        root = resolve_path(search_root) if search_root else str(Path.home())
        rx = _re.compile(pattern, _re.IGNORECASE)
        hits = []
        for p in Path(root).rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in (".exe", ".dll", ".png", ".jpg", ".zip",
                                    ".pdf", ".mp4", ".mp3", ".bin"):
                continue
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{p}:{lineno}: {line.strip()[:120]}")
                            if len(hits) >= max_results:
                                raise StopIteration
            except StopIteration:
                break
            except Exception:
                continue
        if not hits:
            return f"No matches for '{pattern}' under {root}"
        return f"{len(hits)} match(es):\n" + "\n".join(hits)
    except Exception as e:
        return f"Error: {e}"


def open_path(path: str) -> str:
    try:
        full = resolve_path(path)
        if IS_WINDOWS:
            os.startfile(full)
        elif IS_MAC:
            subprocess.Popen(["open", full])
        else:
            subprocess.Popen(["xdg-open", full])
        return f"Opened {full}"
    except Exception as e:
        return f"Error: {e}"


def zip_path(src: str, dst: str = "") -> str:
    import shutil
    try:
        s = resolve_path(src)
        if not dst:
            dst = s.rstrip("/\\") + ".zip"
        else:
            dst = resolve_path(dst)
        base = dst[:-4] if dst.lower().endswith(".zip") else dst
        shutil.make_archive(base, "zip", os.path.dirname(s), os.path.basename(s))
        return f"Created {base}.zip"
    except Exception as e:
        return f"Error: {e}"


def unzip_path(src: str, dst: str = "") -> str:
    import zipfile
    try:
        s = resolve_path(src)
        if not dst:
            dst = os.path.splitext(s)[0]
        else:
            dst = resolve_path(dst)
        os.makedirs(dst, exist_ok=True)
        with zipfile.ZipFile(s) as z:
            z.extractall(dst)
        return f"Extracted to {dst}"
    except Exception as e:
        return f"Error: {e}"


def get_time() -> str:
    return datetime.now().strftime("%A, %B %d, %Y - %I:%M %p")


# ============================================================
#  SYSTEM INFO
# ============================================================

def system_info() -> str:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\" if IS_WINDOWS else "/")
        lines = [
            f"CPU: {cpu}%",
            f"RAM: {mem.percent}% ({mem.used//(1024**3)}/{mem.total//(1024**3)} GB)",
            f"Disk: {disk.percent}% ({disk.used//(1024**3)}/{disk.total//(1024**3)} GB)",
        ]
        try:
            bat = psutil.sensors_battery()
            if bat:
                lines.append(f"Battery: {int(bat.percent)}% "
                             f"({'charging' if bat.power_plugged else 'battery'})")
        except Exception:
            pass
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def battery_status() -> str:
    try:
        import psutil
        bat = psutil.sensors_battery()
        if not bat:
            return "No battery detected (desktop PC)."
        return f"Battery at {int(bat.percent)}%, " \
               f"{'plugged in' if bat.power_plugged else 'on battery'}."
    except Exception as e:
        return f"Error: {e}"


def top_processes(n: int = 5) -> str:
    try:
        import psutil
        n = max(1, min(20, n))
        procs = []
        for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                procs.append((p.info["name"] or "?",
                              p.info["cpu_percent"] or 0,
                              p.info["memory_percent"] or 0))
            except Exception:
                pass
        procs.sort(key=lambda x: x[1], reverse=True)
        lines = [f"{name}: {cpu:.1f}% CPU, {mem:.1f}% RAM"
                 for name, cpu, mem in procs[:n]]
        return "\n".join(lines) or "No process data."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  POWER
# ============================================================

def lock_screen() -> str:
    try:
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.LockWorkStation()
        elif IS_MAC:
            subprocess.run(["pmset", "displaysleepnow"])
        else:
            subprocess.run(["loginctl", "lock-session"])
        return "Screen locked."
    except Exception as e:
        return f"Error: {e}"


def sleep_pc() -> str:
    try:
        if IS_WINDOWS:
            subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
        elif IS_MAC:
            subprocess.run(["pmset", "sleepnow"])
        else:
            subprocess.run(["systemctl", "suspend"])
        return "Going to sleep."
    except Exception as e:
        return f"Error: {e}"


def shutdown_pc(delay_seconds: int = 20, **_ignored) -> str:
    try:
        delay = max(5, min(300, int(delay_seconds or 20)))
    except (TypeError, ValueError):
        delay = 20
    # The delay is left out of the key so changing it between turns does not
    # silently re-arm instead of running.
    go, msg = _confirm_gate("shutdown",
                            "shut this PC down in %d seconds" % delay,
                            "shutdown_pc", {"delay_seconds": delay})
    if not go:
        return msg
    try:
        if IS_WINDOWS:
            subprocess.run(f"shutdown /s /t {delay}", shell=True)
        elif IS_MAC:
            subprocess.run(["osascript", "-e",
                'tell app "System Events" to shut down'])
        else:
            subprocess.run(["shutdown", "-h", f"+{delay//60}"])
        return f"Shutting down in {delay} seconds."
    except Exception as e:
        return f"Error: {e}"


def restart_pc(delay_seconds: int = 20, **_ignored) -> str:
    try:
        delay = max(5, min(300, int(delay_seconds or 20)))
    except (TypeError, ValueError):
        delay = 20
    go, msg = _confirm_gate("restart", "restart this PC in %d seconds" % delay,
                            "restart_pc", {"delay_seconds": delay})
    if not go:
        return msg
    try:
        if IS_WINDOWS:
            subprocess.run(f"shutdown /r /t {delay}", shell=True)
        elif IS_MAC:
            subprocess.run(["osascript", "-e",
                'tell app "System Events" to restart'])
        else:
            subprocess.run(["shutdown", "-r", f"+{delay//60}"])
        return f"Restarting in {delay} seconds."
    except Exception as e:
        return f"Error: {e}"


def abort_shutdown() -> str:
    try:
        if IS_WINDOWS:
            subprocess.run("shutdown /a", shell=True)
        else:
            subprocess.run(["shutdown", "-c"])
        return "Shutdown cancelled."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  AUDIO
# ============================================================

def _press(key: str, presses: int = 1) -> bool:
    try:
        gui = _gui()
        for _ in range(presses):
            gui.press(key)
            time.sleep(0.03)
        return True
    except Exception as e:
        print(f"[key error] {e}")
        return False


def volume_up(steps: int = 5) -> str:
    steps = max(1, min(20, steps))
    return f"Volume +{steps}." if _press("volumeup", steps) else "Failed."


def volume_down(steps: int = 5) -> str:
    steps = max(1, min(20, steps))
    return f"Volume -{steps}." if _press("volumedown", steps) else "Failed."


def mute_toggle() -> str:
    return "Mute toggled." if _press("volumemute") else "Failed."


# ============================================================
#  SCREEN & BRIGHTNESS
# ============================================================

def take_screenshot(save_path: str = "", open_after: bool = False) -> str:
    try:
        gui = _gui()
        if not save_path:
            fname = f"jarvis_shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            save_path = os.path.join(get_desktop_path(), fname)
        else:
            save_path = resolve_path(save_path)
            if not os.path.isabs(save_path) and os.path.dirname(save_path) == "":
                save_path = os.path.join(get_desktop_path(), save_path)

        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img = gui.screenshot()
        img.save(save_path)

        if open_after:
            open_path(save_path)

        return f"Screenshot saved to: {save_path}"
    except Exception as e:
        return f"Screenshot error: {e}"


_NO_DDC = ("This monitor does not support software brightness control "
           "(no DDC/CI). Use the buttons on the monitor itself.")


def _set_brightness(delta: int) -> str:
    """Nudge brightness by delta percent. Shared by up/down."""
    try:
        import screen_brightness_control as sbc
    except Exception:
        return "Brightness control unavailable: screen-brightness-control is not installed."

    try:
        cur = sbc.get_brightness()
    except Exception:
        return _NO_DDC

    # An external monitor on VGA/DVI/HDMI without DDC/CI returns an empty list
    # here. Say so plainly instead of leaking an IndexError at the model.
    if isinstance(cur, list):
        if not cur:
            return _NO_DDC
        cur = cur[0]
    if cur is None:
        return _NO_DDC

    new = max(0, min(100, int(cur) + delta))
    try:
        sbc.set_brightness(new)
    except Exception:
        return _NO_DDC
    return f"Brightness -> {new}%."


def brightness_up(steps: int = 10) -> str:
    return _set_brightness(abs(steps))


def brightness_down(steps: int = 10) -> str:
    return _set_brightness(-abs(steps))


# ============================================================
#  APPS
# ============================================================

APP_MAP = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe",
    "file explorer": "explorer.exe", "cmd": "cmd.exe",
    "command prompt": "cmd.exe", "terminal": "wt.exe",
    "powershell": "powershell.exe", "task manager": "taskmgr.exe",
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "edge": "msedge.exe", "firefox": "firefox.exe",
    "vscode": "code", "vs code": "code", "visual studio code": "code",
    "spotify": "spotify.exe", "discord": "discord.exe",
    "steam": "steam.exe", "slack": "slack.exe",
    "word": "winword.exe", "excel": "excel.exe",
    "powerpoint": "powerpnt.exe", "outlook": "outlook.exe",
    "obs": "obs64.exe", "settings": "ms-settings:",
    "control panel": "control",
}


def open_app(name: str) -> str:
    key = name.lower().strip()
    target = APP_MAP.get(key, key)
    try:
        if IS_WINDOWS:
            subprocess.Popen(f'start "" "{target}"', shell=True)
        elif IS_MAC:
            subprocess.Popen(["open", "-a", target])
        else:
            subprocess.Popen([target])
        return f"Launched {name}."
    except Exception as e:
        return f"Could not open {name}: {e}"


def close_app(name: str) -> str:
    try:
        if IS_WINDOWS:
            exe = APP_MAP.get(name.lower().strip(), name)
            if not exe.endswith(".exe"):
                exe += ".exe"
            r = subprocess.run(f'taskkill /IM "{exe}" /F',
                               shell=True, capture_output=True, text=True)
            out = (r.stdout or r.stderr).strip()
            return f"Closed {name}." if "SUCCESS" in out or "not found" not in out.lower() \
                   else f"{name} is not running."
        else:
            r = subprocess.run(["pkill", "-f", name], capture_output=True)
            return f"Closed {name}." if r.returncode == 0 else f"{name} not running."
    except Exception as e:
        return f"Error: {e}"


def list_running_apps() -> str:
    try:
        import psutil
        skip = {"svchost.exe", "system", "registry", "csrss.exe",
                "wininit.exe", "services.exe", "lsass.exe", "dwm.exe",
                "fontdrvhost.exe", "runtimebroker.exe", "sihost.exe",
                "conhost.exe", "taskhostw.exe", "spoolsv.exe"}
        names = set()
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if n and n not in skip:
                names.add(n)
        return "\n".join(sorted(names))[:4000]
    except Exception as e:
        return f"Error: {e}"


def list_installed_apps() -> str:
    try:
        if not IS_WINDOWS:
            return "Installed-app scan only supported on Windows."
        roots = [
            os.path.join(os.environ.get("ProgramData", ""),
                         r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("APPDATA", ""),
                         r"Microsoft\Windows\Start Menu\Programs"),
        ]
        found = set()
        for root in roots:
            if not os.path.isdir(root):
                continue
            for path in Path(root).rglob("*.lnk"):
                found.add(path.stem)
        if not found:
            return "No Start Menu shortcuts found."
        return "\n".join(sorted(found))[:5000]
    except Exception as e:
        return f"Error: {e}"


def kill_process(name: str, **_ignored) -> str:
    if not str(name or "").strip():
        return "Which process should I stop, sir?"
    go, msg = _confirm_gate("kill:" + str(name).strip().lower(),
                            "force %s to close, losing anything unsaved" % name,
                            "kill_process", {"name": name})
    if not go:
        return msg
    try:
        if IS_WINDOWS:
            exe = name if name.lower().endswith(".exe") else name + ".exe"
            r = subprocess.run(f'taskkill /IM "{exe}" /F',
                               shell=True, capture_output=True, text=True)
            return (r.stdout or r.stderr).strip()[:500] or f"Killed {name}."
        else:
            r = subprocess.run(["pkill", "-f", name], capture_output=True)
            return f"Killed {name}." if r.returncode == 0 else "Not running."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  WINDOW MANAGEMENT
# ============================================================

def show_desktop() -> str:
    try:
        gui = _gui()
        if IS_WINDOWS:
            gui.hotkey("win", "d")
        elif IS_MAC:
            gui.hotkey("fn", "f11")
        else:
            gui.hotkey("super", "d")
        return "Desktop shown."
    except Exception as e:
        return f"Error: {e}"


def switch_window() -> str:
    try:
        _gui().hotkey("alt", "tab")
        return "Switched window."
    except Exception as e:
        return f"Error: {e}"


def minimize_window() -> str:
    try:
        _gui().hotkey("win", "down")
        return "Window minimized."
    except Exception as e:
        return f"Error: {e}"


def maximize_window() -> str:
    try:
        _gui().hotkey("win", "up")
        return "Window maximized."
    except Exception as e:
        return f"Error: {e}"


def close_window() -> str:
    try:
        _gui().hotkey("alt", "f4")
        return "Closed active window."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  KEYBOARD & MOUSE
# ============================================================

def type_text(text: str) -> str:
    try:
        _gui().typewrite(text, interval=0.01)
        return f"Typed {len(text)} characters."
    except Exception as e:
        return f"Error: {e}"


def press_key(key: str, times: int = 1) -> str:
    try:
        gui = _gui()
        for _ in range(max(1, min(50, times))):
            gui.press(key)
            time.sleep(0.05)
        return f"Pressed {key} x {times}."
    except Exception as e:
        return f"Error: {e}"


def press_hotkey(keys: str) -> str:
    try:
        parts = [k.strip().lower() for k in keys.replace("+", " ").split()]
        if not parts:
            return "No keys provided."
        _gui().hotkey(*parts)
        return f"Pressed {'+'.join(parts)}."
    except Exception as e:
        return f"Error: {e}"


def move_mouse(x: int, y: int) -> str:
    try:
        _gui().moveTo(x, y, duration=0.2)
        return f"Mouse moved to ({x}, {y})."
    except Exception as e:
        return f"Error: {e}"


def click_mouse(x: int = -1, y: int = -1, button: str = "left",
                clicks: int = 1) -> str:
    try:
        gui = _gui()
        if x >= 0 and y >= 0:
            gui.moveTo(x, y, duration=0.15)
        gui.click(button=button, clicks=clicks)
        return f"{button.title()} click x {clicks}."
    except Exception as e:
        return f"Error: {e}"


def scroll_screen(amount: int) -> str:
    try:
        _gui().scroll(amount)
        return f"Scrolled {amount}."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  WEB (Windows-safe, reuses existing browser session)
# ============================================================

def _open_url_smart(url: str) -> bool:
    try:
        if IS_WINDOWS:
            os.startfile(url)
            return True
    except Exception as e:
        print(f"[open_url] startfile failed: {e}")
    try:
        if IS_MAC:
            subprocess.Popen(["open", url],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        else:
            subprocess.Popen(["xdg-open", url],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
    except Exception as e:
        print(f"[open_url] native open failed: {e}")
    try:
        import webbrowser
        webbrowser.open(url, new=0, autoraise=True)
        return True
    except Exception as e:
        print(f"[open_url] webbrowser failed: {e}")
        return False


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return f"Opened {url}" if _open_url_smart(url) else f"Could not open {url}"


def web_search(query: str) -> str:
    import urllib.parse
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    return f"Searching Google for: {query}" if _open_url_smart(url) \
           else "Could not open browser."


def youtube_search(query: str) -> str:
    import urllib.parse
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    return f"Searching YouTube for: {query}" if _open_url_smart(url) \
           else "Could not open browser."


# ============================================================
#  MEDIA
# ============================================================

def media_play_pause() -> str:
    return "Play/pause toggled." if _press("playpause") else "Failed."


def media_next() -> str:
    return "Next track." if _press("nexttrack") else "Failed."


def media_previous() -> str:
    return "Previous track." if _press("prevtrack") else "Failed."


# ============================================================
#  CLIPBOARD
# ============================================================

def get_clipboard() -> str:
    try:
        import pyperclip
        text = pyperclip.paste()
        return text[:2000] if text else "(clipboard is empty)"
    except Exception as e:
        return f"Error: {e}"


def set_clipboard(text: str) -> str:
    try:
        import pyperclip
        pyperclip.copy(text)
        return f"Copied {len(text)} characters to clipboard."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  PYTHON
# ============================================================

def run_python(code: str) -> str:
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(code, {"__name__": "__jarvis__"})
        out = buf.getvalue()
        return (f"Output:\n{out[:4000]}" if out else "Ran (no output).")
    except Exception as e:
        return f"Python error: {e}"


# ============================================================
#  NOTIFICATIONS
# ============================================================

def send_notification(title: str, message: str) -> str:
    try:
        if IS_WINDOWS:
            try:
                from win10toast import ToastNotifier
                ToastNotifier().show_toast(title, message, duration=5, threaded=True)
                return f"Notification: {title}"
            except Exception:
                ps = (
                    f'[reflection.assembly]::loadwithpartialname('
                    f'"System.Windows.Forms");'
                    f'$n=New-Object System.Windows.Forms.NotifyIcon;'
                    f'$n.Icon=[System.Drawing.SystemIcons]::Information;'
                    f'$n.Visible=$true;'
                    f'$n.ShowBalloonTip(5000,"{title}","{message}",0);'
                    f'Start-Sleep -Seconds 5;'
                    f'$n.Dispose()'
                )
                subprocess.Popen(["powershell", "-Command", ps])
                return f"Notification: {title}"
        elif IS_MAC:
            subprocess.run(["osascript", "-e",
                f'display notification "{message}" with title "{title}"'])
            return f"Notification: {title}"
        else:
            subprocess.run(["notify-send", title, message])
            return f"Notification: {title}"
    except Exception as e:
        return f"Error: {e}"


def set_timer(seconds: int, label: str = "Timer") -> str:
    seconds = max(1, min(86400, int(seconds)))
    def fire():
        try:
            send_notification("JARVIS", f"{label} - time's up.")
        except Exception:
            pass
    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    mins = seconds // 60
    secs = seconds % 60
    pretty = f"{mins}m {secs}s" if mins else f"{secs}s"
    return f"Timer set for {pretty}."


# ============================================================
#  NETWORK
# ============================================================

def ping_host(host: str = "8.8.8.8", count: int = 4) -> str:
    count = max(1, min(10, count))
    try:
        flag = "-n" if IS_WINDOWS else "-c"
        r = subprocess.run(["ping", flag, str(count), host],
                           capture_output=True, text=True, timeout=20)
        out = (r.stdout or r.stderr).strip()
        return out[:1500] if out else "No ping output."
    except Exception as e:
        return f"Error: {e}"


def get_ip() -> str:
    try:
        import socket
        hostname = socket.gethostname()
        local = socket.gethostbyname(hostname)
        public = "unknown"
        try:
            import urllib.request
            public = urllib.request.urlopen(
                "https://api.ipify.org", timeout=4).read().decode().strip()
        except Exception:
            pass
        return f"Local IP: {local}\nPublic IP: {public}"
    except Exception as e:
        return f"Error: {e}"


def check_port(host: str, port: int) -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        result = s.connect_ex((host, int(port)))
        s.close()
        return f"Port {port} on {host}: {'OPEN' if result == 0 else 'closed/filtered'}"
    except Exception as e:
        return f"Error: {e}"


def flush_dns() -> str:
    try:
        if IS_WINDOWS:
            r = subprocess.run("ipconfig /flushdns", shell=True,
                               capture_output=True, text=True)
            return r.stdout.strip() or "DNS cache flushed."
        elif IS_MAC:
            subprocess.run(["dscacheutil", "-flushcache"])
            subprocess.run(["killall", "-HUP", "mDNSResponder"])
            return "DNS cache flushed."
        else:
            subprocess.run(["systemd-resolve", "--flush-caches"])
            return "DNS cache flushed."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  WEATHER
# ============================================================

def get_weather(location: str = "") -> str:
    try:
        import urllib.request, urllib.parse
        loc = urllib.parse.quote(location) if location else ""
        url = f"https://wttr.in/{loc}?format=%l:+%c+%t,+feels+like+%f,+wind+%w,+humidity+%h"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        data = urllib.request.urlopen(req, timeout=6).read().decode().strip()
        return data or "No weather data."
    except Exception as e:
        return f"Weather unavailable: {e}"


# ============================================================
#  DEV TOOLS
# ============================================================

def git_status(path: str = ".") -> str:
    try:
        p = resolve_path(path)
        r = subprocess.run(["git", "-C", p, "status", "--short", "--branch"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr).strip()[:2000] or "(clean)"
    except Exception as e:
        return f"Error: {e}"


def docker_ps() -> str:
    try:
        r = subprocess.run(["docker", "ps", "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}"],
            capture_output=True, text=True, timeout=10)
        out = (r.stdout or r.stderr).strip()
        return out[:3000] if out else "No containers running."
    except FileNotFoundError:
        return "Docker is not installed."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  RECYCLE BIN
# ============================================================

def empty_recycle_bin(**_ignored) -> str:
    go, msg = _confirm_gate(
        "empty_recycle_bin",
        "permanently empty the Recycle Bin, which cannot be undone",
        "empty_recycle_bin", {})
    if not go:
        return msg
    try:
        if IS_WINDOWS:
            subprocess.run(
                "powershell -Command \"Clear-RecycleBin -Force -ErrorAction SilentlyContinue\"",
                shell=True, capture_output=True)
            return "Recycle Bin emptied."
        elif IS_MAC:
            subprocess.run(["rm", "-rf", os.path.expanduser("~/.Trash/*")], shell=True)
            return "Trash emptied."
        else:
            subprocess.run("rm -rf ~/.local/share/Trash/*", shell=True)
            return "Trash emptied."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  ENVIRONMENT VARIABLES
# ============================================================

def get_env(name: str) -> str:
    val = os.environ.get(name)
    return f"{name} = {val}" if val is not None else f"{name} is not set."


def set_env(name: str, value: str, persistent: bool = False) -> str:
    try:
        os.environ[name] = value
        if persistent and IS_WINDOWS:
            subprocess.run(f'setx {name} "{value}"', shell=True,
                           capture_output=True)
            return f"{name} set persistently (restart shell to take effect)."
        return f"{name} set for this session."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  USER MEMORY
# ============================================================

def remember_fact(key: str, value: str, category: str = "general") -> str:
    """Store a fact about the user for future conversations."""
    if _memory_ref is None:
        return "Memory system not connected."
    try:
        _memory_ref.remember_fact(key, value, category)
        return f"Remembered: {key} -> {value}"
    except Exception as e:
        return f"Error saving fact: {e}"


def forget_fact(key: str) -> str:
    """Delete a stored fact about the user."""
    if _memory_ref is None:
        return "Memory system not connected."
    try:
        _memory_ref.forget_fact(key)
        return f"Forgot: {key}"
    except Exception as e:
        return f"Error: {e}"


def list_user_facts() -> str:
    """List everything JARVIS knows about the user."""
    if _memory_ref is None:
        return "Memory system not connected."
    try:
        summary = _memory_ref.facts_summary()
        return summary if summary else "I don't know anything about you yet, sir."
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  EXTERNAL AI  (uses local Ollama models — no API keys required)
# ============================================================

# Order matters — first match wins.
_DEEPSEEK_LOCAL_CANDIDATES = ["deepseek-r1:8b", "deepseek-r1:7b",
                              "deepseek-r1:14b", "deepseek-r1:1.5b"]
_LLAMA_LOCAL_CANDIDATES    = ["llama3.2:3b", "llama3.2:1b",
                              "llama3.1:8b", "llama3:8b"]
_QWEN_LOCAL_CANDIDATES     = ["qwen2.5:7b", "qwen2.5:14b",
                              "qwen2.5:3b", "qwen2.5:1.5b"]

_AI_TIMEOUT = 60


def _ollama_list_models():
    """Return list of model names currently available in Ollama."""
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True,
                           text=True, timeout=10)
        lines = (r.stdout or "").splitlines()
        names = []
        for line in lines[1:]:  # skip header
            parts = line.split()
            if parts:
                names.append(parts[0])
        return names
    except Exception:
        return []


def _pick_local_model(candidates):
    """Return the first candidate that is actually installed, or None."""
    available = set(_ollama_list_models())
    for c in candidates:
        # exact match
        if c in available:
            return c
        # fuzzy: name starts with candidate prefix (e.g. "deepseek-r1:8b-q4")
        for a in available:
            if a.startswith(c.split(":")[0] + ":") and c.split(":")[1] in a:
                return a
    return None


def _ollama_ask(model: str, question: str, system: str = "") -> str:
    """Query a local Ollama model directly (non-streaming)."""
    try:
        import ollama
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": question})
        resp = ollama.chat(
            model=model,
            messages=messages,
            stream=False,
            options={"num_predict": 400, "temperature": 0.4},
        )
        text = resp["message"]["content"]
        # Strip thinking blocks from R1-style models
        text = re.sub(r" thinking.*? response", "", text, flags=re.DOTALL)
        return text.strip()[:2500]
    except Exception as e:
        return f"Local model error ({model}): {e}"


def _with_folder_context(system: str) -> str:
    """Prepend this machine's real folder map to any AI's system prompt.

    Every ask_* tool funnels through _cloud_or_local, so doing it here means
    no consulted AI has to guess where "the Jarvis AI folder" actually is.
    """
    header = ("Real folder paths on this Windows PC "
              "(use these, never guess or assume Documents):\n" + folder_context())
    return header + ("\n\n" + system if system else "")


def _cloud_or_local(question, system, local_candidates,
                    cloud_fn, cloud_key_env, provider_name):
    """Try local Ollama first, then fall back to cloud if key is set."""
    system = _with_folder_context(system)
    local = _pick_local_model(local_candidates)
    if local:
        result = _ollama_ask(local, question, system)
        return f"[{provider_name} via local {local}] {result}"

    # cloud fallback
    key = os.environ.get(cloud_key_env, "").strip()
    if key:
        return cloud_fn(question, system, key)

    return (f"{provider_name} is not available locally "
            f"(no matching Ollama model) and no {cloud_key_env} is set.")


# ---------- Cloud fallbacks (only used if API key present) ----------

def _post_json(url, headers, body, timeout=_AI_TIMEOUT):
    data = _json.dumps(body).encode("utf-8")
    req = _ur.Request(url, data=data, headers=headers, method="POST")
    try:
        with _ur.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8"))
    except _ue.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")[:400]
        except Exception:
            err_body = str(e)
        raise RuntimeError(f"HTTP {e.code}: {err_body}")


def _cloud_deepseek(question, system, key):
    try:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": question})
        resp = _post_json(
            "https://api.deepseek.com/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {"model": "deepseek-chat", "messages": msgs, "stream": False},
        )
        return "[DeepSeek cloud] " + resp["choices"][0]["message"]["content"][:2500]
    except Exception as e:
        return f"DeepSeek cloud error: {e}"


def _cloud_chatgpt(question, system, key):
    try:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": question})
        resp = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {"model": "gpt-4o-mini", "messages": msgs},
        )
        return "[ChatGPT cloud] " + resp["choices"][0]["message"]["content"][:2500]
    except Exception as e:
        return f"ChatGPT cloud error: {e}"


def _cloud_claude(question, system, key):
    try:
        body = {
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 800,
            "messages": [{"role": "user", "content": question}],
        }
        if system:
            body["system"] = system
        resp = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01",
             "Content-Type": "application/json"},
            body,
        )
        return "[Claude cloud] " + resp["content"][0]["text"][:2500]
    except Exception as e:
        return f"Claude cloud error: {e}"


def _cloud_gemini(question, system, key):
    try:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-1.5-flash:generateContent?key={key}")
        full = (system + "\n\n" + question) if system else question
        resp = _post_json(
            url, {"Content-Type": "application/json"},
            {"contents": [{"parts": [{"text": full}]}]},
        )
        return "[Gemini cloud] " + resp["candidates"][0]["content"]["parts"][0]["text"][:2500]
    except Exception as e:
        return f"Gemini cloud error: {e}"


# ---------- Public tools ----------

def ask_deepseek(question: str, system: str = "") -> str:
    """Ask DeepSeek. Uses the local deepseek-r1 model if available."""
    return _cloud_or_local(
        question, system, _DEEPSEEK_LOCAL_CANDIDATES,
        _cloud_deepseek, "DEEPSEEK_API_KEY", "DeepSeek")


def ask_llama(question: str, system: str = "") -> str:
    """Ask Llama. Uses the local llama3.2 model if available."""
    return _cloud_or_local(
        question, system, _LLAMA_LOCAL_CANDIDATES,
        lambda q, s, k: "Llama cloud not configured.", "LLAMA_API_KEY", "Llama")


def ask_qwen(question: str, system: str = "") -> str:
    """Ask Qwen. Uses the local qwen2.5 model if available."""
    return _cloud_or_local(
        question, system, _QWEN_LOCAL_CANDIDATES,
        lambda q, s, k: "Qwen cloud not configured.", "QWEN_API_KEY", "Qwen")


def ask_chatgpt(question: str, system: str = "") -> str:
    """Ask ChatGPT. Cloud only (no local GPT)."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return ("ChatGPT is a cloud-only service and no OPENAI_API_KEY is set. "
                "Try asking a different local model, or say 'list available AIs'.")
    return _cloud_chatgpt(question, system, key)


def ask_claude(question: str, system: str = "") -> str:
    """Ask Claude. Cloud only (no local Claude)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return ("Claude is a cloud-only service and no ANTHROPIC_API_KEY is set. "
                "Try asking a different local model, or say 'list available AIs'.")
    return _cloud_claude(question, system, key)


def ask_gemini(question: str, system: str = "") -> str:
    """Ask Gemini. Cloud only (no local Gemini)."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return ("Gemini is a cloud-only service and no GEMINI_API_KEY is set. "
                "Try asking a different local model, or say 'list available AIs'.")
    return _cloud_gemini(question, system, key)


def ask_local_model(model_name: str, question: str, system: str = "") -> str:
    """Ask any specific local Ollama model by exact name."""
    system = _with_folder_context(system)
    try:
        available = _ollama_list_models()
        if model_name not in available:
            # try prefix match
            match = None
            for a in available:
                if a.startswith(model_name.split(":")[0]):
                    match = a
                    break
            if not match:
                return (f"Model '{model_name}' not installed. "
                        f"Available: {', '.join(available) or 'none'}")
            model_name = match
        return _ollama_ask(model_name, question, system)
    except Exception as e:
        return f"Error: {e}"


def list_available_ais() -> str:
    """List every AI model Jarvis can consult."""
    lines = []

    local = _ollama_list_models()
    if local:
        lines.append("Local Ollama models you can ask:")
        for m in local:
            lines.append(f"  • {m}")
    else:
        lines.append("No local Ollama models installed.")

    cloud = []
    for env, name in [
        ("DEEPSEEK_API_KEY", "DeepSeek cloud"),
        ("OPENAI_API_KEY", "ChatGPT"),
        ("ANTHROPIC_API_KEY", "Claude"),
        ("GEMINI_API_KEY", "Gemini"),
    ]:
        if os.environ.get(env, "").strip():
            cloud.append(name)

    if cloud:
        lines.append("Cloud APIs configured:")
        for c in cloud:
            lines.append(f"  • {c}")
    else:
        lines.append("No cloud API keys set (not required).")

    return "\n".join(lines)


# ============================================================
#  REGISTRY
# ============================================================

# ============================================================
#  DEV ENVIRONMENT: VS CODE + CLAUDE CODE
# ============================================================

# claude -p routinely takes minutes on a real request, so the 45s used by
# run_shell_command is far too short.
CLAUDE_TIMEOUT = 300
VSCODE_TIMEOUT = 30

# server.py runs with NUM_CTX = 2048. A full Claude Code answer can be several
# thousand characters, which would blow the conversation context, so the model
# gets a trimmed version and the whole thing goes to disk.
_CLAUDE_OUT_CAP = 1200
_CLAUDE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "claude_last.txt")

_VSCODE_FALLBACKS = (
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
    r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
)
_CLAUDE_FALLBACKS = (
    os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
        r"\Anthropic.ClaudeCode_Microsoft.Winget.Source_8wekyb3d8bbwe\claude.exe"),
)


def _which(name: str, fallbacks=()):
    """Locate an executable. uvicorn may start with a thinner PATH than the
    shell, and both of these live in user-local folders, so fall back to the
    known install locations rather than just failing."""
    import shutil
    hit = shutil.which(name)
    if hit:
        return hit
    for f in fallbacks:
        if f and os.path.isfile(f):
            return f
    return None


def open_in_vscode(path: str = "") -> str:
    """Open a folder (default: this project) in VS Code."""
    exe = _which("code", _VSCODE_FALLBACKS)
    if not exe:
        return "The VS Code command line ('code') was not found on this PC."
    folder = resolve_path(path) if path else PROJECT_ROOT
    if not os.path.exists(folder):
        return path_not_found(path or "the project folder")
    try:
        subprocess.run([exe, folder], capture_output=True, text=True,
                       timeout=VSCODE_TIMEOUT)
        return f"VS Code opened on {folder}."
    except subprocess.TimeoutExpired:
        return f"VS Code was launched on {folder}."
    except Exception as e:
        return f"Could not open VS Code: {e}"


# Folders Claude Code may READ. Deliberately not the whole home directory.
def _claude_read_dirs() -> list:
    wanted = ("project (Jarvis AI)", "desktop", "documents", "downloads")
    folders = known_folders()
    return [folders[k] for k in wanted if k in folders and os.path.isdir(folders[k])]


def _run_claude(request: str, folder: str, mode: str, extra_dirs=()) -> str:
    exe = _which("claude", _CLAUDE_FALLBACKS)
    if not exe:
        return "The Claude Code CLI ('claude') was not found on this PC."
    if not str(request).strip():
        return "I need to know what to ask Claude Code, sir."

    cwd = resolve_path(folder) if folder else PROJECT_ROOT
    if not os.path.isdir(cwd):
        return path_not_found(folder or "the project folder")

    cmd = [exe, "-p", str(request), "--permission-mode", mode]
    for d in extra_dirs:
        if os.path.isdir(d) and os.path.normpath(d) != os.path.normpath(cwd):
            cmd += ["--add-dir", d]

    # Hand Claude Code the real folder map. Not knowing where things actually
    # live is what made the assistant guess "your documents directory".
    cmd += ["--append-system-prompt",
            "Folder map for this machine (use these real paths, never guess):\n"
            + folder_context()]

    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=CLAUDE_TIMEOUT, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        return (f"Claude Code did not finish within {CLAUDE_TIMEOUT // 60} minutes. "
                f"That request is probably too large to run by voice.")
    except Exception as e:
        return f"Claude Code error: {e}"

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    text = out or err or f"(no output) exit={r.returncode}"

    try:
        os.makedirs(os.path.dirname(_CLAUDE_LOG), exist_ok=True)
        with open(_CLAUDE_LOG, "w", encoding="utf-8") as f:
            f.write(f"mode: {mode}\ncwd:  {cwd}\nrequest: {request}\n\n{text}\n")
    except Exception:
        pass

    if len(text) > _CLAUDE_OUT_CAP:
        text = (text[:_CLAUDE_OUT_CAP].rstrip() +
                " ... (full answer saved to backend/data/claude_last.txt)")
    return text


def ask_claude_code(request: str, folder: str = "") -> str:
    """Ask Claude Code about the codebase. Read-only - changes nothing."""
    return _run_claude(request, folder, "plan", _claude_read_dirs())


# ---------- TWO-TURN CONFIRMATION ----------
# A `confirm: bool` parameter is NOT a safety gate. The model fills it in, and
# qwen2.5:7b was observed setting confirm=true on its very first call and
# editing a file nobody had approved. Every destructive tool therefore routes
# through _confirm_gate, which the model cannot satisfy on its own: the action
# is ARMED on one turn and can only RUN on a LATER turn, and only when the
# user's own words in that later turn were affirmative.
_TURN = 0
_LAST_USER_TEXT = ""
_PENDING = None            # {"key":..., "what":..., "turn":int}

_AFFIRMATIVE = (
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "do it", "go ahead", "proceed", "please do", "affirmative", "make it so",
    "approved", "permission granted", "run it",
)
_NEGATIVE = ("no", "don't", "dont", "stop", "cancel", "never mind",
             "nevermind", "abort", "wait")


def begin_turn(user_text: str = "") -> None:
    """Called by brain.py once per user message. Defines the turn boundary."""
    global _TURN, _LAST_USER_TEXT
    _TURN += 1
    _LAST_USER_TEXT = str(user_text or "")


# An armed action must not linger. Once replay_pending() exists, a stale pending
# request turns any later "yes" - even a yes to an unrelated question - into a
# trigger for something the user has long since moved on from.
PENDING_TTL_TURNS = 3


def _user_said_no() -> bool:
    s = _LAST_USER_TEXT.strip().lower()
    return bool(s) and any(n in s for n in _NEGATIVE)


def _pending_expired() -> bool:
    return (_PENDING is not None
            and (_TURN - _PENDING["turn"]) > PENDING_TTL_TURNS)


def _drop_expired_pending() -> None:
    global _PENDING
    if _pending_expired():
        _PENDING = None


def _user_said_yes() -> bool:
    s = _LAST_USER_TEXT.strip().lower()
    if not s:
        return False
    # An explicit refusal always wins, even in a sentence containing "yes".
    if any(n in s for n in _NEGATIVE):
        return False
    return any(a in s for a in _AFFIRMATIVE)


def _confirm_gate(key: str, what: str, tool: str = "", args: dict = None):
    """Returns (go_ahead, message_to_relay).

    `key` identifies the exact action, so approving one thing cannot authorise a
    different one. `what` is phrased to be read aloud. `tool` and `args` record
    how to re-invoke this exact call, so replay_pending() can finish the job
    when the user says yes - see the pre-router note in brain.py.
    """
    global _PENDING
    _drop_expired_pending()
    armed = (_PENDING is not None
             and _PENDING["key"] == key
             and _PENDING["turn"] < _TURN)

    if armed and _user_said_yes():
        _PENDING = None
        return True, ""

    if armed:
        # Armed, but this turn was not a yes. Keep waiting; do not act.
        return False, ("Still waiting for your go-ahead to %s. "
                       "Say yes to confirm, or no to cancel." % what)

    _PENDING = {"key": key, "what": what, "turn": _TURN,
                "tool": tool, "args": dict(args or {})}
    return False, ("This will %s. Ask the user to confirm out loud, then call "
                   "this tool again." % what)


def replay_pending():
    """Re-invoke the armed call so a confirmed action actually completes.

    This does NOT bypass the gate. The gate lives inside the tool and is
    evaluated again on this call: it still requires a later turn AND the user's
    own affirmative words. All this does is guarantee the tool gets CALLED,
    which is the part the model was unreliable at.

    Returns (what_was_done, tool_output), or None when nothing is armed.
    """
    _drop_expired_pending()
    if not _PENDING:
        return None
    tool = _PENDING.get("tool")
    if not tool or tool not in TOOL_REGISTRY:
        return None
    what = _PENDING.get("what", "that")
    args = dict(_PENDING.get("args") or {})
    try:
        return what, TOOL_REGISTRY[tool](**args)
    except Exception as e:
        return what, "That failed: %s" % e


def pending_action() -> str:
    """What destructive action, if any, is waiting for the user's go-ahead."""
    _drop_expired_pending()
    if not _PENDING:
        return "Nothing is awaiting confirmation."
    return "Awaiting your confirmation to %s." % _PENDING["what"]


def cancel_pending_action() -> str:
    """Drop whatever is waiting for confirmation."""
    global _PENDING
    if not _PENDING:
        return "There was nothing waiting for confirmation."
    what = _PENDING["what"]
    _PENDING = None
    return "Cancelled. I will not %s." % what


def claude_code_edit(request: str, folder: str = "", **_ignored) -> str:
    """Let Claude Code change files. Needs confirmation across two turns.

    Any `confirm` argument the model invents is swallowed and ignored.
    """
    if not str(request).strip():
        return "I need to know what to ask Claude Code to change, sir."

    target = resolve_path(folder) if folder else PROJECT_ROOT
    if not os.path.isdir(target):
        return path_not_found(folder or "the project folder")

    go, msg = _confirm_gate(
        "claude_edit:%s:%s" % (_squash(str(request)), os.path.normpath(target).lower()),
        "let Claude Code edit files in %s" % target,
        "claude_code_edit", {"request": request, "folder": folder})
    if not go:
        return msg
    # Writes stay scoped to the target folder - no --add-dir on purpose.
    return _run_claude(request, folder, "acceptEdits")


def where_is(name: str) -> str:
    """Resolve a spoken folder or file name to its real path on this PC."""
    full = resolve_path(name)
    if os.path.isdir(full):
        return f"'{name}' is a folder at {full}"
    if os.path.isfile(full):
        return f"'{name}' is a file at {full}"
    return path_not_found(name)


def list_known_folders() -> str:
    """The real paths of the folders JARVIS is usually asked about."""
    return folder_context()


# ============================================================
#  SPEECH: PACE AND VOICE
# ============================================================

def set_speech_pace(direction: str = "", rate: float = 0) -> str:
    """Change how fast JARVIS speaks.

    `direction` takes faster / slower / normal. `rate` sets it outright, where
    1.0 is normal and 1.3 is thirty percent quicker.
    """
    s = _speech()
    if s is None:
        return "Speech control is not available."
    try:
        if rate:
            return s.set_rate(rate)
        if direction:
            return s.nudge_rate(direction)
        return s.describe_rate()
    except Exception as e:
        return f"Could not change the speaking pace: {e}"


def get_speech_pace() -> str:
    s = _speech()
    return s.describe_rate() if s else "Speech control is not available."


def set_voice(name: str) -> str:
    """Switch to another installed Piper voice."""
    s = _speech()
    if s is None:
        return "Speech control is not available."
    try:
        return s.set_voice(name)
    except Exception as e:
        return f"Could not change voice: {e}"


def list_voices() -> str:
    """Which voices are installed, and which one is active."""
    s = _speech()
    if s is None:
        return "Speech control is not available."
    names = s.voice_names()
    if not names:
        return "No voice models were found in models/piper."
    return ("Installed voices: " + ", ".join(names) +
            f". Currently using {s.voice_name}.")


# ============================================================
#  WEB ANSWERS (spoken search)
# ============================================================
#
# web_search() opens a browser tab on the PC, which is useless over voice.
# web_answer() fetches and condenses so the reply can be SPOKEN.
#
# Sources, tried in order, most authoritative first:
#   1. Brave Search API   real general web search, only if BRAVE_API_KEY is set
#   2. DuckDuckGo Instant Answer   keyless and official, but only answers
#      encyclopedic questions; returns nothing for technical or local queries
#   3. Wikipedia search + summary  keyless and official, reliable, but it is an
#      encyclopedia and not the web
#
# Deliberately NOT used: scraping duckduckgo.com/html or lite.duckduckgo.com.
# Both now serve a CAPTCHA to this machine. Working around a bot check is not
# something to build into a tool.
#
# Snippets are kept short on purpose: server.py runs NUM_CTX = 2048, and a tool
# has no business pasting long passages of someone else's text into the model.

_WEB_TIMEOUT = 12
_WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_SNIPPET_CAP = 220          # per result
_ANSWER_CAP = 900           # whole spoken reply
_SEARCH_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "last_search.txt")


def _web_get(url: str, headers: dict = None, timeout: int = _WEB_TIMEOUT) -> str:
    h = {"User-Agent": _WEB_UA}
    if headers:
        h.update(headers)
    req = _ur.Request(url, headers=h)
    with _ur.urlopen(req, timeout=timeout) as r:
        return r.read(200000).decode("utf-8", "replace")


def _web_json(url: str, headers: dict = None) -> dict:
    return _json.loads(_web_get(url, headers))


def _tidy(s: str, cap: int = _SNIPPET_CAP) -> str:
    """Collapse whitespace, strip tags, and cut at a word boundary."""
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= cap:
        return s
    cut = s[:cap]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > cap * 0.6 else cut).rstrip(" ,;:.") + "..."


_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is",
    "are", "was", "were", "be", "been", "what", "who", "when", "where", "why",
    "how", "which", "that", "this", "it", "its", "do", "does", "did", "can",
    "best", "good", "top", "near", "me", "my", "your", "about", "with", "from",
    # Words from the ASKING, not the subject. Counting "look" as a subject term
    # scored an unrelated article as a 0.67 match.
    "look", "tell", "find", "search", "know", "give", "want", "please",
    "jarvis", "up", "out", "there", "any", "much", "many", "old", "tall", "far",
}

# Natural-language question framing has to come OFF before searching.
# Wikipedia's search matches the literal phrase, so "what is the capital of
# France" returned "Closed-ended question" and "What Is a Nation?" - it matched
# on "What Is". Stripping the preamble gives "capital of France", which returns
# "List of capitals of France".
_QUERY_PREFIX = re.compile(
    r"^\s*(?:(?:hey\s+)?jarvis[,\s]+)?"
    r"(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?"
    r"(?:(?:go\s+)?(?:and\s+)?(?:look\s*up|look\s+for|search\s+(?:the\s+web\s+)?for|"
    r"search|find\s+out\s+(?:about)?|find|tell\s+me\s+(?:about)?|"
    r"do\s+you\s+know|what\s+do\s+you\s+know\s+about)\s+)?"
    r"(?:(?:what|who|which|whose)\s+(?:is|are|was|were)\s+(?:the\s+)?|"
    r"(?:when|where|why)\s+(?:is|are|was|were|did|do|does)\s+|"
    r"how\s+(?:many|much|tall|old|far|long)\s+)?",
    re.IGNORECASE)


def _search_query(q: str) -> str:
    """Turn a spoken question into something a search engine handles well."""
    s = re.sub(r"\s+", " ", str(q or "")).strip()
    s = s.rstrip("?!. ")
    trimmed = _QUERY_PREFIX.sub("", s, count=1).strip()
    # Never strip it down to nothing, or to a single noise word.
    return trimmed if len(trimmed) >= 3 else s


def _terms(s: str) -> set:
    words = re.findall(r"[a-z0-9]+", str(s or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _relevance(query: str, text: str) -> float:
    """Share of the query's meaningful words that appear in the result.

    Wikipedia's search always returns SOMETHING, so without this a question it
    cannot answer comes back as a confident, unrelated article - "python asyncio
    gather" returned "Coroutine". Reading that out as the answer is the same
    fabrication problem as the model inventing a file listing.
    """
    q = _terms(query)
    if not q:
        return 1.0
    found = _terms(text)
    return len([w for w in q if w in found]) / float(len(q))


WEAK_MATCH = 0.34           # below this, do not present it as the answer


# ---------- source 1: Brave (only with a key) ----------
def _search_brave(query: str, key: str):
    url = ("https://api.search.brave.com/res/v1/web/search?q="
           + _uq.quote(_search_query(query)) + "&count=4")
    data = _web_json(url, {"Accept": "application/json",
                           "X-Subscription-Token": key})
    out = []
    for item in (data.get("web", {}).get("results") or [])[:4]:
        title = _tidy(item.get("title", ""), 90)
        desc = _tidy(item.get("description", ""))
        if title or desc:
            out.append((title, desc, item.get("url", ""), "Brave"))
    return out


# ---------- source 2: DuckDuckGo Instant Answer ----------
def _search_ddg_instant(query: str):
    url = ("https://api.duckduckgo.com/?q=" + _uq.quote(_search_query(query))
           + "&format=json&no_html=1&skip_disambig=1")
    d = _web_json(url)
    answer = _tidy(d.get("Answer") or "", _ANSWER_CAP)
    abstract = _tidy(d.get("AbstractText") or d.get("Abstract") or "", 600)
    source = d.get("AbstractSource") or "DuckDuckGo"
    # The source NAME is reported separately, so do not repeat it in the title -
    # that produced "According to DuckDuckGo: Wikipedia - ...".
    if answer:
        return [("", answer, d.get("AbstractURL", ""), source)]
    if abstract:
        return [("", abstract, d.get("AbstractURL", ""), source)]
    return []


# ---------- source 3: Wikipedia ----------
def _search_wikipedia(query: str):
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
           "&srsearch=" + _uq.quote(_search_query(query)) + "&format=json&srlimit=3")
    hits = (_web_json(url).get("query", {}) or {}).get("search") or []
    out = []
    for h in hits[:3]:
        title = h.get("title", "")
        if not title:
            continue
        # The REST summary endpoint needs an exact title, which is why the
        # search step above cannot be skipped.
        try:
            s = _web_json("https://en.wikipedia.org/api/rest_v1/page/summary/"
                          + _uq.quote(title.replace(" ", "_")))
            extract = _tidy(s.get("extract", ""), 420)
            link = (s.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
        except Exception:
            extract = _tidy(h.get("snippet", ""))
            link = "https://en.wikipedia.org/wiki/" + _uq.quote(title.replace(" ", "_"))
        if extract:
            out.append((title, extract, link, "Wikipedia"))
    # Wikipedia's own ordering is not relevance for our purposes: it offered
    # "Capital punishment in France" next to "List of capitals of France".
    out.sort(key=lambda r: -_relevance(query, r[0] + " " + r[1]))
    return out[:2]


def web_answer(query: str) -> str:
    """Search the web and return a short spoken answer, with its source."""
    q = (query or "").strip()
    if not q:
        return "What would you like me to look up, sir?"

    attempts = []
    results = []

    brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
    chain = []
    if brave_key:
        chain.append(("Brave", lambda: _search_brave(q, brave_key)))
    chain.append(("DuckDuckGo", lambda: _search_ddg_instant(q)))
    chain.append(("Wikipedia", lambda: _search_wikipedia(q)))

    for name, fn in chain:
        try:
            results = fn()
            if results:
                break
            attempts.append(name + ": nothing")
        except Exception as e:
            attempts.append("%s: %s" % (name, type(e).__name__))
            results = []

    if not results:
        detail = "; ".join(attempts) if attempts else "no sources responded"
        hint = ("" if brave_key else
                " For general web search, set BRAVE_API_KEY and I can use it.")
        return ("I could not find an answer for '%s'. (%s)%s"
                % (_tidy(q, 80), detail, hint))

    # Does what came back actually address the question?
    best = max(_relevance(q, (r[0] or "") + " " + r[1]) for r in results)
    weak = best < WEAK_MATCH

    try:
        os.makedirs(os.path.dirname(_SEARCH_LOG), exist_ok=True)
        with open(_SEARCH_LOG, "w", encoding="utf-8") as f:
            f.write("query:     %s\nrelevance: %.2f%s\n\n"
                    % (q, best, " (WEAK)" if weak else ""))
            for title, body, link, src in results:
                f.write("[%s] %s\n%s\n%s\n\n" % (src, title, body, link))
    except Exception:
        pass

    src = results[0][3]
    parts = []
    for title, body, _link, _s in results:
        parts.append(("%s - %s" % (title, body)) if title else body)
    text = " | ".join(parts)
    if len(text) > _ANSWER_CAP:
        text = text[:_ANSWER_CAP].rstrip() + "..."

    if weak:
        # Say plainly that this is not an answer, so it is not read out as one.
        return ("I did not find a direct answer to '%s'. The closest thing %s "
                "has is: %s. Tell the user this may not be what they asked for."
                % (_tidy(q, 70), src, text))

    return "According to %s: %s" % (src, text)


def search_sources() -> str:
    """Which search backends are usable right now."""
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    lines = [
        "Brave web search: " + ("available" if key
                                else "needs BRAVE_API_KEY (not set)"),
        "DuckDuckGo instant answers: available, best for factual questions",
        "Wikipedia: available",
    ]
    return " | ".join(lines)


TOOL_REGISTRY = {
    # files & shell
    "run_shell_command": run_shell_command,
    "read_file":         read_file,
    "write_file":        write_file,
    "list_dir":          list_dir,
    "create_folder":     create_folder,
    "move_path":         move_path,
    "copy_path":         copy_path,
    "delete_path":       delete_path,
    "find_files":        find_files,
    "grep_files":        grep_files,
    "open_path":         open_path,
    "zip_path":          zip_path,
    "unzip_path":        unzip_path,
    "get_time":          get_time,
    # info
    "system_info":       system_info,
    "battery_status":    battery_status,
    "top_processes":     top_processes,
    # power
    "lock_screen":       lock_screen,
    "sleep_pc":          sleep_pc,
    "shutdown_pc":       shutdown_pc,
    "restart_pc":        restart_pc,
    "abort_shutdown":    abort_shutdown,
    # audio
    "volume_up":         volume_up,
    "volume_down":       volume_down,
    "mute_toggle":       mute_toggle,
    # screen
    "take_screenshot":   take_screenshot,
    "brightness_up":     brightness_up,
    "brightness_down":   brightness_down,
    # apps
    "open_app":          open_app,
    "close_app":         close_app,
    "list_running_apps": list_running_apps,
    "list_installed_apps": list_installed_apps,
    "kill_process":      kill_process,
    # window
    "show_desktop":      show_desktop,
    "switch_window":     switch_window,
    "minimize_window":   minimize_window,
    "maximize_window":   maximize_window,
    "close_window":      close_window,
    # input
    "type_text":         type_text,
    "press_key":         press_key,
    "press_hotkey":      press_hotkey,
    "move_mouse":        move_mouse,
    "click_mouse":       click_mouse,
    "scroll_screen":     scroll_screen,
    # web
    "open_url":          open_url,
    "web_search":        web_search,
    "web_answer":        web_answer,
    "search_sources":    search_sources,
    "youtube_search":    youtube_search,
    # media
    "media_play_pause":  media_play_pause,
    "media_next":        media_next,
    "media_previous":    media_previous,
    # clipboard
    "get_clipboard":     get_clipboard,
    "set_clipboard":     set_clipboard,
    # python
    "run_python":        run_python,
    # notify & timer
    "send_notification": send_notification,
    "set_timer":         set_timer,
    # network
    "ping_host":         ping_host,
    "get_ip":            get_ip,
    "check_port":        check_port,
    "flush_dns":         flush_dns,
    # weather
    "get_weather":       get_weather,
    # dev
    "git_status":        git_status,
    "docker_ps":         docker_ps,
    # recycle
    "empty_recycle_bin": empty_recycle_bin,
    # env
    "get_env":           get_env,
    "set_env":           set_env,
    # user memory
    "remember_fact":     remember_fact,
    "forget_fact":       forget_fact,
    "list_user_facts":   list_user_facts,
    # speech: pace and voice
    "set_speech_pace":   set_speech_pace,
    "get_speech_pace":   get_speech_pace,
    "set_voice":         set_voice,
    "list_voices":       list_voices,
    # dev environment: VS Code + Claude Code
    "open_in_vscode":    open_in_vscode,
    "ask_claude_code":   ask_claude_code,
    "claude_code_edit":  claude_code_edit,
    "cancel_pending_action": cancel_pending_action,
    "pending_action":    pending_action,
    # path discovery - stops any AI having to guess where folders are
    "where_is":          where_is,
    "list_known_folders": list_known_folders,
    # external AI
    "ask_deepseek":      ask_deepseek,
    "ask_llama":         ask_llama,
    "ask_qwen":          ask_qwen,
    "ask_chatgpt":       ask_chatgpt,
    "ask_claude":        ask_claude,
    "ask_gemini":        ask_gemini,
    "ask_local_model":   ask_local_model,
    "list_available_ais": list_available_ais,
}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object",
            "properties": props or {},
            "required": required or []}}}


TOOL_SCHEMAS = [
    # speech: pace and voice
    _fn("set_speech_pace",
        "Change how fast you speak. Use direction 'faster', 'slower' or "
        "'normal', or set rate outright where 1.0 is normal and 1.3 is 30% "
        "quicker. Call this when the user asks you to speed up or slow down.",
        {"direction": {"type": "string"},
         "rate": {"type": "number"}}, []),
    _fn("get_speech_pace", "Report how fast you are currently speaking."),
    _fn("set_voice",
        "Switch to a different installed speaking voice by name.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("list_voices", "List the installed speaking voices."),
    # dev environment: VS Code + Claude Code
    _fn("open_in_vscode",
        "Open a folder in Visual Studio Code. Leave path empty for this "
        "JARVIS project, or say 'my desktop', 'documents', or a full path.",
        {"path": {"type": "string"}}, []),
    _fn("ask_claude_code",
        "Ask Claude Code a question about the code in a folder. READ-ONLY: it "
        "explains and answers but never changes files. Use this for 'ask Claude "
        "Code what X does' or 'ask Claude Code how to fix Y'.",
        {"request": {"type": "string"},
         "folder": {"type": "string"}}, ["request"]),
    _fn("claude_code_edit",
        "Let Claude Code MODIFY files in a folder. Destructive. The first call "
        "only ARMS the request and returns a message: relay it, ask the user "
        "out loud, and call this again on their next message. It will not run "
        "until the user has actually said yes.",
        {"request": {"type": "string"},
         "folder": {"type": "string"}}, ["request"]),
    _fn("cancel_pending_action",
        "Cancel whatever destructive action is waiting for confirmation. Call "
        "this when the user says no."),
    _fn("pending_action",
        "Report which destructive action, if any, is awaiting confirmation."),
    _fn("where_is",
        "Find the real full path of a folder or file by its spoken name, e.g. "
        "'Jarvis AI', 'memories.txt', 'my downloads'. Use this before saying a "
        "folder does not exist.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("list_known_folders",
        "List the real paths of this PC's main folders: the JARVIS project, "
        "Desktop, Documents, Downloads and home."),
    # files & shell
    _fn("run_shell_command", "Run a shell command",
        {"command": {"type": "string"}}, ["command"]),
    _fn("read_file", "Read a file's contents",
        {"path": {"type": "string"}}, ["path"]),
    _fn("write_file", "Write text to a file",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"]),
    _fn("list_dir",
        "List files in a folder. Use 'my desktop', 'my documents', "
        "'my downloads', or a full path.",
        {"path": {"type": "string"}}, ["path"]),
    _fn("create_folder", "Create a folder",
        {"path": {"type": "string"}}, ["path"]),
    _fn("move_path", "Move or rename a file/folder",
        {"src": {"type": "string"}, "dst": {"type": "string"}},
        ["src", "dst"]),
    _fn("copy_path", "Copy a file/folder",
        {"src": {"type": "string"}, "dst": {"type": "string"}},
        ["src", "dst"]),
    _fn("delete_path",
        "Send a file or folder to the Recycle Bin. Destructive: the first call "
        "only ARMS it and returns a message to relay. Ask the user, then call "
        "again on their next message.",
        {"path": {"type": "string"}}, ["path"]),
    _fn("find_files", "Find files matching a glob pattern",
        {"pattern": {"type": "string"}, "search_root": {"type": "string"}},
        ["pattern"]),
    _fn("grep_files", "Search file contents for a pattern",
        {"pattern": {"type": "string"}, "search_root": {"type": "string"},
         "max_results": {"type": "integer"}},
        ["pattern"]),
    _fn("open_path", "Open a file or folder with the default app",
        {"path": {"type": "string"}}, ["path"]),
    _fn("zip_path", "Zip a file or folder",
        {"src": {"type": "string"}, "dst": {"type": "string"}}, ["src"]),
    _fn("unzip_path", "Extract a .zip archive",
        {"src": {"type": "string"}, "dst": {"type": "string"}}, ["src"]),
    _fn("get_time", "Current date and time"),

    # info
    _fn("system_info", "CPU, RAM, disk, battery status"),
    _fn("battery_status", "Battery percentage and charging state"),
    _fn("top_processes", "Top processes by CPU usage",
        {"n": {"type": "integer"}}),

    # power
    _fn("lock_screen", "Lock the screen"),
    _fn("sleep_pc", "Put the PC to sleep"),
    _fn("shutdown_pc",
        "Shut down the PC. Destructive: the first call only ARMS it and returns "
        "a message to relay. Ask the user, then call again on their next reply.",
        {"delay_seconds": {"type": "integer"}}, []),
    _fn("restart_pc",
        "Restart the PC. Destructive: the first call only ARMS it and returns a "
        "message to relay. Ask the user, then call again on their next reply.",
        {"delay_seconds": {"type": "integer"}}, []),
    _fn("abort_shutdown", "Cancel a pending shutdown"),

    # audio
    _fn("volume_up", "Increase volume",
        {"steps": {"type": "integer"}}),
    _fn("volume_down", "Decrease volume",
        {"steps": {"type": "integer"}}),
    _fn("mute_toggle", "Toggle mute"),

    # screen
    _fn("take_screenshot",
        "Capture the screen. Saves to the real Desktop folder.",
        {"save_path": {"type": "string"}, "open_after": {"type": "boolean"}}),
    _fn("brightness_up", "Increase screen brightness",
        {"steps": {"type": "integer"}}),
    _fn("brightness_down", "Decrease screen brightness",
        {"steps": {"type": "integer"}}),

    # apps
    _fn("open_app", "Launch an app by name",
        {"name": {"type": "string"}}, ["name"]),
    _fn("close_app", "Close a running app by name",
        {"name": {"type": "string"}}, ["name"]),
    _fn("list_running_apps", "List running apps"),
    _fn("list_installed_apps", "List installed apps from Start Menu"),
    _fn("kill_process",
        "Force an application to close, losing unsaved work. Destructive: the "
        "first call only ARMS it and returns a message to relay. Ask the user, "
        "then call again on their next reply.",
        {"name": {"type": "string"}}, ["name"]),

    # window
    _fn("show_desktop", "Minimize all windows to show the desktop"),
    _fn("switch_window", "Alt+Tab"),
    _fn("minimize_window", "Minimize active window"),
    _fn("maximize_window", "Maximize active window"),
    _fn("close_window", "Close active window"),

    # input
    _fn("type_text", "Type text into the focused window",
        {"text": {"type": "string"}}, ["text"]),
    _fn("press_key", "Press a single key (e.g. enter, tab, esc, f5)",
        {"key": {"type": "string"}, "times": {"type": "integer"}},
        ["key"]),
    _fn("press_hotkey", "Press a hotkey combo, e.g. 'ctrl+c'",
        {"keys": {"type": "string"}}, ["keys"]),
    _fn("move_mouse", "Move mouse to (x, y)",
        {"x": {"type": "integer"}, "y": {"type": "integer"}},
        ["x", "y"]),
    _fn("click_mouse", "Click mouse (optional x/y, button, clicks)",
        {"x": {"type": "integer"}, "y": {"type": "integer"},
         "button": {"type": "string"}, "clicks": {"type": "integer"}}),
    _fn("scroll_screen", "Scroll up (positive) or down (negative)",
        {"amount": {"type": "integer"}}, ["amount"]),

    # web
    _fn("open_url", "Open a URL in the default browser",
        {"url": {"type": "string"}}, ["url"]),
    _fn("web_search",
        "OPEN a browser tab on the PC with search results. The user cannot hear "
        "these; use it only when they ask to open or show a search.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("web_answer",
        "Look something up online and get a short answer you can READ ALOUD. "
        "Use this whenever the user asks a question about the world - news, "
        "facts, people, places, definitions - rather than opening a browser.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("search_sources", "Report which web search backends are available."),
    _fn("youtube_search", "Search YouTube",
        {"query": {"type": "string"}}, ["query"]),

    # media
    _fn("media_play_pause", "Play/pause media"),
    _fn("media_next", "Next track"),
    _fn("media_previous", "Previous track"),

    # clipboard
    _fn("get_clipboard", "Read clipboard"),
    _fn("set_clipboard", "Copy text to clipboard",
        {"text": {"type": "string"}}, ["text"]),

    # python
    _fn("run_python", "Execute a short Python snippet",
        {"code": {"type": "string"}}, ["code"]),

    # notify & timer
    _fn("send_notification", "Show a desktop notification",
        {"title": {"type": "string"}, "message": {"type": "string"}},
        ["title", "message"]),
    _fn("set_timer", "Set a notification timer",
        {"seconds": {"type": "integer"}, "label": {"type": "string"}},
        ["seconds"]),

    # network
    _fn("ping_host", "Ping a host",
        {"host": {"type": "string"}, "count": {"type": "integer"}}),
    _fn("get_ip", "Get local and public IP addresses"),
    _fn("check_port", "Check if a TCP port is open",
        {"host": {"type": "string"}, "port": {"type": "integer"}},
        ["host", "port"]),
    _fn("flush_dns", "Flush the DNS resolver cache"),

    # weather
    _fn("get_weather", "Get current weather (optionally for a city)",
        {"location": {"type": "string"}}),

    # dev
    _fn("git_status", "Run git status in a directory",
        {"path": {"type": "string"}}),
    _fn("docker_ps", "List running Docker containers"),

    # recycle
    _fn("empty_recycle_bin",
        "Permanently empty the Recycle Bin; this cannot be undone. Destructive: "
        "the first call only ARMS it and returns a message to relay. Ask the "
        "user, then call again on their next reply."),

    # env
    _fn("get_env", "Read an environment variable",
        {"name": {"type": "string"}}, ["name"]),
    _fn("set_env", "Set an environment variable",
        {"name": {"type": "string"}, "value": {"type": "string"},
         "persistent": {"type": "boolean"}},
        ["name", "value"]),

    # user memory
    _fn("remember_fact",
        "Save a fact about the user (name, preference, project, etc.) "
        "for future chats. Call this whenever the user shares personal information.",
        {"key": {"type": "string"}, "value": {"type": "string"},
         "category": {"type": "string"}},
        ["key", "value"]),
    _fn("forget_fact", "Delete a stored fact about the user",
        {"key": {"type": "string"}}, ["key"]),
    _fn("list_user_facts", "List everything you know about the user"),

    # external AI  (local Ollama by default)
    _fn("ask_deepseek",
        "Consult DeepSeek for a question. Uses the local deepseek-r1 model — no key needed.",
        {"question": {"type": "string"}, "system": {"type": "string"}},
        ["question"]),
    _fn("ask_llama",
        "Consult Llama for a question. Uses the local llama3.2 model.",
        {"question": {"type": "string"}, "system": {"type": "string"}},
        ["question"]),
    _fn("ask_qwen",
        "Consult Qwen for a question. Uses the local qwen2.5 model.",
        {"question": {"type": "string"}, "system": {"type": "string"}},
        ["question"]),
    _fn("ask_chatgpt",
        "Consult OpenAI ChatGPT. Requires OPENAI_API_KEY.",
        {"question": {"type": "string"}, "system": {"type": "string"}},
        ["question"]),
    _fn("ask_claude",
        "Consult Anthropic Claude. Requires ANTHROPIC_API_KEY.",
        {"question": {"type": "string"}, "system": {"type": "string"}},
        ["question"]),
    _fn("ask_gemini",
        "Consult Google Gemini. Requires GEMINI_API_KEY.",
        {"question": {"type": "string"}}, ["question"]),
    _fn("ask_local_model",
        "Ask any specific local Ollama model by name",
        {"model_name": {"type": "string"}, "question": {"type": "string"},
         "system": {"type": "string"}},
        ["model_name", "question"]),
    _fn("list_available_ais",
        "List every AI model Jarvis can consult (local + cloud)"),
]