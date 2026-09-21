"""
JARVIS end-to-end command test.

selftest_tools.py proves each tool works when called directly. This proves the
harder half: that the model actually *decides* to call the right tool when you
speak a command, instead of describing it or replying with prose.

    python test_commands.py              # the default read-only command set
    python test_commands.py --verbose    # also print each full reply
    python test_commands.py --report out.txt

Nothing is deleted and no key is pressed. Two caveats worth knowing:
  - the VS Code cases really do launch `code` on the project folder (if it is
    already open, this just focuses the existing window)
  - the "ask claude code" case really does run `claude -p` in read-only plan
    mode, which takes ~15s and costs tokens
Add cases to CASES as you teach JARVIS new phrasings.

Each case is (spoken_command, expected_tools, required).
    expected_tools   any one of these counts as correct
    required         True  -> a tool MUST be called (it is a PC action)
                     False -> no tool should be called (it is small talk)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools
from brain import JarvisBrain
from memory import JarvisMemory

CASES = [
    # --- plain info ---------------------------------------------------
    ("what time is it",                    ["get_time"],                  True),
    ("how much RAM am I using",            ["system_info"],               True),
    ("what are my top processes",          ["top_processes"],             True),

    # --- files --------------------------------------------------------
    ("list my desktop",                    ["list_dir"],                  True),
    ("what's in my downloads folder",      ["list_dir"],                  True),

    # --- apps ---------------------------------------------------------
    ("what apps are running",              ["list_running_apps"],         True),

    # --- network ------------------------------------------------------
    ("what's my IP address",               ["get_ip"],                    True),
    ("ping google.com",                    ["ping_host"],                 True),

    # --- weather ------------------------------------------------------
    ("what's the weather",                 ["get_weather"],               True),

    # --- clipboard ----------------------------------------------------
    ("what's in my clipboard",             ["get_clipboard"],             True),

    # --- memory (the fact is removed again at the end of the run) -----
    ("remember that my test colour is teal",
                                           ["remember_fact"],             True),
    ("what do you know about me",          ["list_user_facts",
                                            "remember_fact"],             True),

    # --- external AI --------------------------------------------------
    ("which AIs can you talk to",          ["list_available_ais"],        True),

    # --- paths: the "folder does not exist" regression -----------------
    # These are the exact phrasings that made JARVIS claim the Jarvis AI folder
    # was missing and offer to create one in Documents.
    ("where is the Jarvis AI folder",      ["where_is", "list_dir",
                                            "list_known_folders"],        True),
    ("list the Jarvis AI folder",          ["list_dir", "where_is"],      True),

    # --- dev environment ----------------------------------------------
    ("open VS Code on the Jarvis AI folder",
                                           ["open_in_vscode"],            True),
    ("open the project in visual studio code",
                                           ["open_in_vscode"],            True),
    # "ask claude code" must NOT fall through to ask_claude, which is a local
    # Ollama model and not Claude Code at all.
    ("ask claude code what memory.py does",
                                           ["ask_claude_code"],           True),

    # --- spoken web search --------------------------------------------
    # These measured 0/4 before the deterministic pre-router: the model answered
    # from stale memory and invented "according to Wikipedia" citations.
    ("who is the prime minister of japan",  ["web_answer"],                True),
    ("how many moons does mars have",       ["web_answer"],                True),
    ("look up the capital of france",       ["web_answer"],                True),
    # ...while a request to OPEN a search must still open a browser tab.
    ("open google for me",                  ["web_search", "open_url"],    True),
    ("search youtube for jazz music",       ["youtube_search"],            True),

    # --- speech pace and voice ----------------------------------------
    ("talk faster",                        ["set_speech_pace"],           True),
    ("you are speaking too fast, slow down",
                                           ["set_speech_pace"],           True),
    ("what voices do you have",            ["list_voices"],               True),

    # --- small talk: calling a tool here is the bug --------------------
    ("hello",                              [],                            False),
    ("how are you",                        [],                            False),
    ("tell me a joke",                     [],                            False),
    ("thanks",                             [],                            False),
]

# Facts a test run may create, cleaned up afterwards.
TEST_FACT_HINTS = ("test colour", "test color", "_selftest")


def run_case(brain, command):
    """Return (called_tools, reply_text, seconds)."""
    msgs = brain.new_conversation()
    msgs.append({"role": "user", "content": command})
    called, reply = [], ""
    t0 = time.time()
    for kind, payload in brain.stream_with_tools(msgs):
        if kind == "tool":
            called.append(payload)
        elif kind == "done":
            reply = payload
    return called, reply, time.time() - t0


def cleanup(mem):
    removed = []
    for key, _value, _cat in mem.conn.execute(
            "SELECT key, value, category FROM facts").fetchall():
        if any(h in key.lower() for h in TEST_FACT_HINTS):
            mem.forget_fact(key)
            removed.append(key)
    return removed


def main():
    verbose = "--verbose" in sys.argv
    report_path = ""
    if "--report" in sys.argv:
        i = sys.argv.index("--report")
        if i + 1 < len(sys.argv):
            report_path = sys.argv[i + 1]

    mem = JarvisMemory()
    tools.register_memory(mem)
    brain = JarvisBrain()

    lines = []

    def emit(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(s.encode(enc, "replace").decode(enc, "replace"))
        lines.append(s)

    emit("JARVIS end-to-end command test")
    emit("model      " + brain.model)
    emit("cases      {}".format(len(CASES)))
    emit("-" * 78)

    passed = failed = 0
    failures = []

    for command, expected, required in CASES:
        try:
            called, reply, secs = run_case(brain, command)
        except Exception as e:
            failed += 1
            detail = "{}: {}".format(type(e).__name__, e)
            failures.append((command, detail))
            emit("FAIL  {:38} {}".format(repr(command), detail))
            continue

        if required:
            ok = any(c in expected for c in called)
            want = "one of " + str(expected)
        else:
            ok = not called
            want = "no tool"

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
            failures.append((command, "wanted {}, got {}".format(want, called or "none")))

        emit("{}  {:38} {:5.1f}s  tools={}".format(
            status, repr(command), secs, called or "-"))
        if verbose or not ok:
            emit("        reply: " + " ".join(reply.split())[:150])

    emit("-" * 78)
    removed = cleanup(mem)
    if removed:
        emit("cleaned up test facts: {}".format(removed))
    emit("PASS {}   FAIL {}".format(passed, failed))
    if failures:
        emit("")
        emit("Failures:")
        for c, d in failures:
            emit("  - {!r}: {}".format(c, d))

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\nreport written to " + report_path)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
