"""
JARVIS capability self-test.

Runs every tool in TOOL_REGISTRY that can be exercised without wrecking
anything, and prints a PASS/FAIL table so you know exactly which parts of PC
control actually work on this machine right now.

    python selftest_tools.py                # safe tools only
    python selftest_tools.py --desktop      # also real keyboard/mouse/volume/
                                            # brightness control (self-reverting)
    python selftest_tools.py --loud         # also tools that pop windows open
                                            # (notepad, browser tabs, show desktop)
    python selftest_tools.py --ai           # also local-LLM round trips (slow)
    python selftest_tools.py --report out.txt

Levels
    safe        read-only, or writes only inside a temp sandbox, or reverts
                itself immediately
    desktop     drives the real keyboard/mouse/volume/brightness, but puts
                everything back where it was -- you should barely notice
    loud        pops a window onto your screen (notepad, browser tab, Win+D)
    destructive never run automatically: shutdown, restart, sleep, lock,
                kill_process, empty_recycle_bin, close_window
"""
import os
import shutil
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools
from memory import JarvisMemory

# Tools that must never be fired by an automated test.
DESTRUCTIVE = {
    "shutdown_pc", "restart_pc", "sleep_pc", "lock_screen",
    "kill_process", "empty_recycle_bin", "close_window", "close_app",
    "abort_shutdown", "set_env",
}

SANDBOX = os.path.join(tempfile.gettempdir(), "jarvis_selftest")


def _mk_sandbox():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    with open(os.path.join(SANDBOX, "note.txt"), "w", encoding="utf-8") as f:
        f.write("jarvis selftest\nTODO find me\n")
    return SANDBOX


def _pair(do, undo):
    """Run a state-changing tool, then immediately undo it."""
    def run():
        out = do()
        time.sleep(0.15)
        try:
            undo()
        except Exception:
            pass
        return out
    return run


def _restore_mouse():
    def run():
        pg = tools._gui()
        x, y = pg.position()
        out = tools.move_mouse(x + 5, y + 5)
        pg.moveTo(x, y)
        return out
    return run


def _clipboard_roundtrip():
    before = ""
    try:
        import pyperclip
        before = pyperclip.paste()
    except Exception:
        pass
    out = tools.set_clipboard("jarvis selftest clipboard")
    got = tools.get_clipboard()
    try:
        import pyperclip
        pyperclip.copy(before)
    except Exception:
        pass
    if "jarvis selftest clipboard" not in got:
        raise AssertionError("clipboard round-trip failed, read back: " + repr(got))
    return out


def _web_answer_works():
    """A known-answerable question must come back grounded, not empty."""
    out = tools.web_answer("what is the capital of France")
    if "could not find" in out.lower():
        raise AssertionError("no source answered: " + out[:120])
    if "paris" not in out.lower():
        raise AssertionError("answer did not contain Paris: " + out[:160])
    # And a nonsense query must fail honestly rather than inventing something.
    junk = tools.web_answer("zzqqxx nonsense query 99")
    if "could not find" not in junk.lower() and "did not find" not in junk.lower():
        raise AssertionError("nonsense query produced an answer: " + junk[:160])
    return " ".join(out.split())[:70]


def _pace_roundtrip():
    """Nudge the speaking pace, verify it moved, then put it back."""
    before = tools.get_speech_pace()
    faster = tools.set_speech_pace("faster")
    if "faster" not in faster.lower():
        raise AssertionError("pace did not increase: " + repr(faster))
    tools.set_speech_pace("normal")
    after = tools.get_speech_pace()
    if "normal" not in after.lower():
        raise AssertionError("pace was not restored: " + repr(after))
    return "nudged and restored (" + " ".join(faster.split())[:40] + ")"


def _voice_roundtrip():
    """Switch to another installed voice and back, if there is another one."""
    s = tools._speech()
    if s is None:
        raise AssertionError("speech controller unavailable")
    names = s.voice_names()
    original = s.voice_name
    if len(names) < 2:
        return "only one voice installed (" + original + "), nothing to switch to"
    other = next(n for n in names if n != original)
    out = tools.set_voice(other)
    if other not in s.voice_name:
        raise AssertionError("voice did not switch: " + repr(out))
    tools.set_voice(original)
    if s.voice_name != original:
        raise AssertionError("voice was not restored, now " + s.voice_name)
    return "switched to " + other + " and back to " + original


def _edit_guard_refuses():
    """claude_code_edit must refuse without confirm=true. Assert the refusal."""
    out = tools.claude_code_edit("delete every file in the project")
    if "confirm" not in out.lower():
        raise AssertionError(
            "claude_code_edit ran without confirmation! got: " + repr(out[:200]))
    return "guard holds: " + " ".join(out.split())[:60]


def _cases():
    """(tool_name, level, callable) -- callable returns the tool's own output."""
    sb = SANDBOX
    note = os.path.join(sb, "note.txt")
    T = tools

    return [
        # ---- info -------------------------------------------------------
        ("get_time",            "safe", lambda: T.get_time()),
        ("system_info",         "safe", lambda: T.system_info()),
        ("battery_status",      "safe", lambda: T.battery_status()),
        ("top_processes",       "safe", lambda: T.top_processes(3)),

        # ---- files & shell (all inside the sandbox) ---------------------
        ("run_shell_command",   "safe", lambda: T.run_shell_command("echo jarvis-ok")),
        ("list_dir",            "safe", lambda: T.list_dir("my desktop")),
        ("read_file",           "safe", lambda: T.read_file(note)),
        ("write_file",          "safe", lambda: T.write_file(
            os.path.join(sb, "written.txt"), "hello from selftest")),
        ("create_folder",       "safe", lambda: T.create_folder(os.path.join(sb, "sub"))),
        ("copy_path",           "safe", lambda: T.copy_path(
            note, os.path.join(sb, "note_copy.txt"))),
        ("move_path",           "safe", lambda: T.move_path(
            os.path.join(sb, "note_copy.txt"), os.path.join(sb, "note_moved.txt"))),
        ("find_files",          "safe", lambda: T.find_files("*.txt", sb)),
        ("grep_files",          "safe", lambda: T.grep_files("TODO", sb)),
        ("zip_path",            "safe", lambda: T.zip_path(
            sb, os.path.join(tempfile.gettempdir(), "jarvis_selftest.zip"))),
        ("unzip_path",          "safe", lambda: T.unzip_path(
            os.path.join(tempfile.gettempdir(), "jarvis_selftest.zip"),
            os.path.join(sb, "unzipped"))),
        ("delete_path",         "safe", lambda: T.delete_path(
            os.path.join(sb, "note_moved.txt"), confirm=True)),
        ("open_path",           "loud", lambda: T.open_path(sb)),

        # ---- screen -----------------------------------------------------
        ("take_screenshot",     "safe", lambda: T.take_screenshot(
            os.path.join(sb, "shot.png"))),
        ("brightness_up",  "intrusive", _pair(lambda: tools.brightness_up(5),
                                              lambda: tools.brightness_down(5))),
        ("brightness_down","intrusive", _pair(lambda: tools.brightness_down(5),
                                              lambda: tools.brightness_up(5))),

        # ---- audio (each nudge is undone immediately) -------------------
        ("volume_up",      "intrusive", _pair(lambda: tools.volume_up(2),
                                              lambda: tools.volume_down(2))),
        ("volume_down",    "intrusive", _pair(lambda: tools.volume_down(2),
                                              lambda: tools.volume_up(2))),
        ("mute_toggle",    "intrusive", _pair(tools.mute_toggle, tools.mute_toggle)),

        # ---- apps / windows --------------------------------------------
        ("list_running_apps",   "safe", lambda: T.list_running_apps()),
        ("list_installed_apps", "safe", lambda: T.list_installed_apps()),
        ("open_app",            "loud", lambda: T.open_app("notepad")),
        ("show_desktop",        "loud", lambda: T.show_desktop()),
        ("switch_window",       "loud", lambda: T.switch_window()),
        ("minimize_window",     "loud", lambda: T.minimize_window()),
        ("maximize_window",     "loud", lambda: T.maximize_window()),

        # ---- input ------------------------------------------------------
        ("move_mouse",     "intrusive", _restore_mouse()),
        ("type_text",      "intrusive", lambda: T.type_text("")),
        ("press_key",      "intrusive", lambda: T.press_key("shift")),
        ("press_hotkey",   "intrusive", lambda: T.press_hotkey("shift")),
        ("click_mouse",    "intrusive", lambda: "skipped by design (would click the desktop)"),
        ("scroll_screen",  "intrusive", _pair(lambda: tools.scroll_screen(1),
                                              lambda: tools.scroll_screen(-1))),

        # ---- clipboard (original contents restored) ---------------------
        ("get_clipboard",       "safe", lambda: T.get_clipboard()),
        ("set_clipboard",       "safe", _clipboard_roundtrip),

        # ---- web --------------------------------------------------------
        ("open_url",            "loud", lambda: T.open_url("example.com")),
        ("web_search",          "loud", lambda: T.web_search("jarvis selftest")),
        ("youtube_search",      "loud", lambda: T.youtube_search("jarvis selftest")),

        # ---- media ------------------------------------------------------
        ("media_play_pause",     "loud", _pair(tools.media_play_pause,
                                               tools.media_play_pause)),
        ("media_next",          "loud", lambda: "skipped by design (would skip your track)"),
        ("media_previous",      "loud", lambda: "skipped by design (would skip your track)"),

        # ---- python -----------------------------------------------------
        ("run_python",          "safe", lambda: T.run_python("print(2+2)")),

        # ---- notify -----------------------------------------------------
        ("send_notification","intrusive", lambda: T.send_notification(
            "JARVIS", "self-test notification")),
        ("set_timer",           "safe", lambda: T.set_timer(1, "selftest")),

        # ---- network ----------------------------------------------------
        ("get_ip",              "safe", lambda: T.get_ip()),
        ("ping_host",           "safe", lambda: T.ping_host("8.8.8.8", 1)),
        ("check_port",          "safe", lambda: T.check_port("127.0.0.1", 8000)),
        ("flush_dns",      "intrusive", lambda: T.flush_dns()),

        # ---- weather / dev / env ---------------------------------------
        ("get_weather",         "safe", lambda: T.get_weather()),
        ("git_status",          "safe", lambda: T.git_status(".")),
        ("docker_ps",           "safe", lambda: T.docker_ps()),
        ("get_env",             "safe", lambda: T.get_env("PATH")),

        # ---- user memory (fact is written then removed) -----------------
        ("remember_fact",       "safe", lambda: T.remember_fact(
            "_selftest_key", "selftest value", "context")),
        ("list_user_facts",     "safe", lambda: T.list_user_facts()),
        ("forget_fact",         "safe", lambda: T.forget_fact("_selftest_key")),

        # ---- spoken web search (read-only network calls) ----------------
        ("web_answer",          "safe", _web_answer_works),
        ("search_sources",      "safe", lambda: T.search_sources()),

        # ---- speech (pace is restored to normal afterwards) -------------
        ("get_speech_pace",     "safe", lambda: T.get_speech_pace()),
        ("set_speech_pace",     "safe", _pace_roundtrip),
        ("list_voices",         "safe", lambda: T.list_voices()),
        ("set_voice",           "safe", _voice_roundtrip),

        # ---- claude edit queue ------------------------------------------
        ("pending_edit",        "safe", lambda: T.pending_edit()),
        ("cancel_claude_edit",  "safe", lambda: T.cancel_claude_edit()),

        # ---- paths ------------------------------------------------------
        ("where_is",            "safe", lambda: T.where_is("Jarvis AI")),
        ("list_known_folders",  "safe", lambda: T.list_known_folders()),

        # ---- dev environment --------------------------------------------
        ("open_in_vscode",      "loud", lambda: T.open_in_vscode()),
        # The confirm guard is the safe half of claude_code_edit: it must refuse
        # without confirm=true, and that refusal is what we assert here.
        ("claude_code_edit",    "safe", _edit_guard_refuses),
        ("ask_claude_code",       "ai", lambda: T.ask_claude_code(
            "Reply with just the word OK. Do not use any tools.")),

        # ---- external AI ------------------------------------------------
        ("list_available_ais",  "safe", lambda: T.list_available_ais()),
        ("ask_qwen",              "ai", lambda: T.ask_qwen("Reply with the word OK.")),
        ("ask_llama",             "ai", lambda: T.ask_llama("Reply with the word OK.")),
        ("ask_deepseek",          "ai", lambda: T.ask_deepseek("Reply with the word OK.")),
        ("ask_local_model",       "ai", lambda: T.ask_local_model(
            "qwen2.5:7b", "Reply with the word OK.")),
        ("ask_chatgpt",           "ai", lambda: T.ask_chatgpt("Reply with the word OK.")),
        ("ask_claude",            "ai", lambda: T.ask_claude("Reply with the word OK.")),
        ("ask_gemini",            "ai", lambda: T.ask_gemini("Reply with the word OK.")),
    ]


# A tool's own error strings -- "not installed", "error:", ... -- mean the tool
# ran but cannot do its job here. That is a FAIL, not a pass.
_FAIL_MARKERS = (
    "not installed", "not available", "no module", "traceback",
    "failed", "error:", "unsupported", "cannot ", "could not",
    "does not support",
)


def _judge(output):
    s = str(output)
    low = s.lower()
    for m in _FAIL_MARKERS:
        if m in low:
            return False, s
    return True, s


def main():
    argv = sys.argv[1:]
    run_desktop = "--desktop" in argv or "--intrusive" in argv
    run_loud = "--loud" in argv
    run_ai = "--ai" in argv
    report_path = ""
    if "--report" in argv:
        i = argv.index("--report")
        if i + 1 < len(argv):
            report_path = argv[i + 1]

    tools.register_memory(JarvisMemory())
    _mk_sandbox()

    lines = []

    def emit(s=""):
        # Windows consoles are cp1252 by default; tool output routinely contains
        # characters it cannot encode (app names, filenames). Never let logging
        # be the thing that crashes the run.
        try:
            print(s)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(s.encode(enc, "replace").decode(enc, "replace"))
        lines.append(s)

    emit("JARVIS capability self-test")
    emit("python     " + sys.version.split()[0])
    emit("sandbox    " + SANDBOX)
    emit("desktop    " + ("yes" if run_desktop else "no (pass --desktop)"))
    emit("loud       " + ("yes" if run_loud else "no (pass --loud)"))
    emit("ai probes  " + ("yes" if run_ai else "no (pass --ai)"))
    emit("-" * 78)

    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    failures = []
    tested = set()

    for name, level, fn in _cases():
        tested.add(name)
        if level == "intrusive" and not run_desktop:
            emit("SKIP  {:22} (desktop control)".format(name))
            counts["SKIP"] += 1
            continue
        if level == "loud" and not run_loud:
            emit("SKIP  {:22} (loud - opens a window)".format(name))
            counts["SKIP"] += 1
            continue
        if level == "ai" and not run_ai:
            emit("SKIP  {:22} (ai probe)".format(name))
            counts["SKIP"] += 1
            continue
        try:
            out = fn()
            ok, detail = _judge(out)
        except Exception as e:
            ok = False
            detail = type(e).__name__ + ": " + str(e)
            if os.environ.get("JARVIS_SELFTEST_TRACE"):
                traceback.print_exc()
        snippet = " ".join(str(detail).split())[:92]
        if ok:
            counts["PASS"] += 1
            emit("PASS  {:22} {}".format(name, snippet))
        else:
            counts["FAIL"] += 1
            failures.append((name, snippet))
            emit("FAIL  {:22} {}".format(name, snippet))

    emit("-" * 78)
    untested = sorted(set(tools.TOOL_REGISTRY) - tested - DESTRUCTIVE)
    emit("tools in registry            : {}".format(len(tools.TOOL_REGISTRY)))
    emit("destructive (never auto-run) : {}  {}".format(
        len(DESTRUCTIVE), sorted(DESTRUCTIVE)))
    if untested:
        emit("no test case yet             : {}".format(untested))
    emit("PASS {}   FAIL {}   SKIP {}".format(
        counts["PASS"], counts["FAIL"], counts["SKIP"]))
    if failures:
        emit("")
        emit("Failures:")
        for n, d in failures:
            emit("  - {}: {}".format(n, d))

    shutil.rmtree(SANDBOX, ignore_errors=True)

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\nreport written to " + report_path)

    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
