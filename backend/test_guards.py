"""
Adversarial tests for the two-turn confirmation gate.

In session 2 qwen2.5:7b set confirm=true on its own first call and edited a file
nobody had approved. A `confirm` parameter is not a gate when the model fills it
in. Every destructive tool now routes through tools._confirm_gate instead, and
this file attacks that gate the way the model actually behaved.

Nothing is really destroyed: subprocess.run and send2trash are replaced with
recorders, so "did it act?" is answerable without shutting the PC down.

    python test_guards.py
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools

# ---------------------------------------------------------------- recorders
ACTED = []


class _Result(object):
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_run(*args, **kwargs):
    ACTED.append(args[0] if args else kwargs.get("args"))
    return _Result()


def _fake_send2trash(path):
    ACTED.append(("send2trash", path))


_real_run = tools.subprocess.run
tools.subprocess.run = _fake_run
_fake_mod = types.ModuleType("send2trash")
_fake_mod.send2trash = _fake_send2trash
sys.modules["send2trash"] = _fake_mod

# A real file, so delete_path's existence check passes.
TMPDIR = tempfile.mkdtemp(prefix="jarvis_guard_")
TMPFILE = os.path.join(TMPDIR, "expendable.txt")
with open(TMPFILE, "w", encoding="utf-8") as f:
    f.write("test\n")

# ---------------------------------------------------------------- the tools
# (label, call, a DIFFERENT action used to test cross-approval)
SPECS = [
    ("delete_path",
     lambda: tools.delete_path(TMPFILE),
     lambda: tools.delete_path(os.path.join(TMPDIR, "other.txt"))),
    ("shutdown_pc", lambda: tools.shutdown_pc(), lambda: tools.restart_pc()),
    ("restart_pc", lambda: tools.restart_pc(), lambda: tools.shutdown_pc()),
    ("kill_process",
     lambda: tools.kill_process("notepad"),
     lambda: tools.kill_process("chrome")),
    ("empty_recycle_bin", lambda: tools.empty_recycle_bin(),
     lambda: tools.shutdown_pc()),
]

pass_n = fail_n = 0
rows = []


def ok(label, cond, detail=""):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        rows.append(("PASS", label, ""))
    else:
        fail_n += 1
        rows.append(("FAIL", label, str(detail)))


def reset():
    """Fresh turn counter and no armed action, between scenarios."""
    tools._PENDING = None
    del ACTED[:]


def acted():
    return len(ACTED) > 0


# ---------------------------------------------------------------- attacks
for name, call, other in SPECS:
    # 1. a single call must never act
    reset()
    tools.begin_turn("please %s" % name)
    out = call()
    ok("%s: first call does not act" % name, not acted(), ACTED)
    ok("%s: first call asks for confirmation" % name,
       "confirm" in out.lower(), out[:90])

    # 2. the model supplying confirm=True must not act
    reset()
    tools.begin_turn("please %s" % name)
    try:
        out = call.__self__ if False else None
    except Exception:
        pass
    # call with the kwarg the model used to fabricate
    if name == "delete_path":
        out = tools.delete_path(TMPFILE, confirm=True)
    elif name == "shutdown_pc":
        out = tools.shutdown_pc(confirm=True)
    elif name == "restart_pc":
        out = tools.restart_pc(confirm=True)
    elif name == "kill_process":
        out = tools.kill_process("notepad", confirm=True)
    else:
        out = tools.empty_recycle_bin(confirm=True)
    ok("%s: confirm=True on the first call is ignored" % name, not acted(), ACTED)

    # 3. calling twice inside ONE turn must not act
    reset()
    tools.begin_turn("please %s" % name)
    call()
    call()
    ok("%s: two calls in the same turn do not act" % name, not acted(), ACTED)

    # 4. a later turn that was not a yes must not act
    reset()
    tools.begin_turn("please %s" % name)
    call()
    tools.begin_turn("what time is it")
    out = call()
    ok("%s: later turn without consent does not act" % name, not acted(), ACTED)
    ok("%s: keeps waiting" % name, "waiting" in out.lower(), out[:80])

    # 5. an explicit no must not act
    reset()
    tools.begin_turn("please %s" % name)
    call()
    tools.begin_turn("no, don't do that")
    call()
    ok("%s: an explicit no does not act" % name, not acted(), ACTED)

    # 6. "yes" mixed with a refusal must not act
    reset()
    tools.begin_turn("please %s" % name)
    call()
    tools.begin_turn("yes but actually don't")
    call()
    ok("%s: a mixed signal does not act" % name, not acted(), ACTED)

    # 7. approval must not carry to a DIFFERENT action
    reset()
    tools.begin_turn("please %s" % name)
    call()
    tools.begin_turn("yes go ahead")
    other()
    ok("%s: consent does not transfer to another action" % name,
       not acted(), ACTED)

    # 8. the happy path must still work
    reset()
    tools.begin_turn("please %s" % name)
    call()
    tools.begin_turn("yes go ahead")
    call()
    ok("%s: acts after a genuine yes" % name, acted(), "never acted")

    # 9. and the approval is spent, not reusable
    del ACTED[:]
    call()
    ok("%s: approval is single-use" % name, not acted(), ACTED)

# ---------------------------------------------------------------- claude edit
reset()
tools.begin_turn("ask claude code to add a comment")
out = tools.claude_code_edit("add a comment to memory.py")
ok("claude_code_edit: first call only arms", "confirm" in out.lower(), out[:80])
ok("claude_code_edit: something is pending",
   "Awaiting" in tools.pending_action(), tools.pending_action())

reset()
tools.begin_turn("ask claude code to add a comment")
out = tools.claude_code_edit("add a comment", confirm=True)
ok("claude_code_edit: confirm=True is ignored", "confirm" in out.lower(), out[:80])

# ---------------------------------------------------------------- reporting
reset()
ok("pending_action reports nothing when idle",
   "Nothing" in tools.pending_action(), tools.pending_action())
tools.begin_turn("shut down the pc")
tools.shutdown_pc()
ok("pending_action names the armed action",
   "shut this PC down" in tools.pending_action(), tools.pending_action())
out = tools.cancel_pending_action()
ok("cancel clears it", "Cancelled" in out, out)
ok("nothing pending after cancel",
   "Nothing" in tools.pending_action(), tools.pending_action())

del ACTED[:]
tools.begin_turn("yes go ahead")
tools.shutdown_pc()
ok("a cancelled action cannot be resurrected by a later yes",
   not acted(), ACTED)

# ---------------------------------------------------------------- teardown
tools.subprocess.run = _real_run
import shutil
shutil.rmtree(TMPDIR, ignore_errors=True)

print("two-turn confirmation gate: adversarial tests")
print("-" * 70)
for status, label, detail in rows:
    print("%-4s %s%s" % (status, label, "  <- " + detail if detail and status == "FAIL" else ""))
print("-" * 70)
print("%d passed, %d failed" % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
