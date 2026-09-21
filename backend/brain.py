import ollama, re
from typing import Generator
import tools as _tools
from tools import TOOL_SCHEMAS, TOOL_REGISTRY, begin_turn

MODEL = "qwen2.5:7b"
NUM_CTX = 2048

SYSTEM_PROMPT = (
    "You are JARVIS, an advanced AI assistant inspired by Iron Man. "
    "You are helpful, precise, and slightly witty. Address the user as 'sir' occasionally. "
    "Reply ONLY in English, in 1-3 short sentences.\n\n"

    "ANTI-REPETITION RULES (strict):\n"
    "- Never repeat the user's question back to them.\n"
    "- Never say the same sentence twice in one reply.\n"
    "- Never restate what you already said earlier in the reply.\n"
    "- Answer each thing exactly once. Do not summarize your own answer.\n"
    "- Do not open with 'You asked...' or 'Sure, here's...'.\n\n"

    "GROUND TRUTH (the most important rule):\n"
    "- You have NO direct knowledge of this PC. Every fact about it - files, "
    "folders, clipboard contents, running processes, installed apps, IP, battery, "
    "time, weather - must come from a tool result you have actually received.\n"
    "- NEVER invent, guess, or illustrate PC data. No placeholder names like "
    "'file1.pdf', 'example text' or 'Document.docx'. A made-up answer about "
    "the user's own machine is the worst mistake you can make.\n"
    "- If you have not yet seen a tool result for what was asked, call the tool "
    "NOW instead of answering.\n"
    "- If no tool can answer it, say you cannot check that - never improvise.\n"
    "- The same applies to the WORLD, not just this PC. Current office-holders, "
    "news, prices, scores, dates, populations: you do not know these, your "
    "training is old, and you must call web_answer. Answering from memory and "
    "sounding certain is the failure mode to avoid.\n"
    "- NEVER write 'according to' or name a source unless that source came "
    "back from a tool you actually called in this conversation. Inventing an "
    "attribution is worse than admitting you do not know.\n\n"

    "LOOKING THINGS UP:\n"
    "- For a question about the world - news, facts, people, places, "
    "definitions - call web_answer and say what it returns. It gives you a "
    "short answer you can speak.\n"
    "- Only use web_search when the user asks you to OPEN or SHOW a search; it "
    "opens a browser tab on the PC and they cannot hear it.\n"
    "- Name the source in your reply, for example 'according to Wikipedia'. "
    "Summarise in one or two sentences; never read a long passage aloud.\n"
    "- If web_answer found nothing, say so plainly. Do NOT fall back on memory "
    "and dress it up as a search result.\n"
    "- If web_answer says it found no direct answer, tell the user that, and "
    "that what it did find may not be what they asked for.\n\n"

    "YOUR VOICE:\n"
    "- If the user asks you to talk faster or slower, call set_speech_pace. "
    "Do not just promise to; the tool is what actually changes it.\n"
    "- Keep the confirmation to one short sentence, so they hear the new pace "
    "rather than a speech about it.\n\n"

    "FOLDERS AND PATHS:\n"
    "- Folder names you hear are resolved for you. 'Jarvis AI', 'the project', "
    "'my desktop', 'documents' and 'downloads' all map to real paths.\n"
    "- NEVER tell the user a folder or file does not exist until you have called "
    "where_is on it. The tool searches the project, Desktop, Documents, "
    "Downloads and home.\n"
    "- NEVER guess or state WHERE something is. If you need a location, call "
    "where_is or list_known_folders.\n"
    "- Never offer to create a folder that you have not first failed to find "
    "with where_is.\n\n"

    "CLAUDE CODE (routing - get this right):\n"
    "- 'ask Claude Code ...' always means a claude_code_* tool, never "
    "ask_claude (which is a different, local model).\n"
    "- If the user wants Claude Code to EXPLAIN, DESCRIBE, REVIEW or ANSWER "
    "something, call ask_claude_code.\n"
    "- If the user wants Claude Code to CREATE, ADD, WRITE, FIX, CHANGE, "
    "REFACTOR, RENAME or DELETE anything, call claude_code_edit.\n"
    "- NEVER refuse an edit request and NEVER claim you cannot change files. "
    "claude_code_edit handles its own safety: the first call only arms the "
    "request and gives you a message to relay. Say that message to the user, "
    "and when they reply yes, call claude_code_edit again with the SAME "
    "request. It physically cannot edit anything until they agree.\n"
    "- If an edit is awaiting confirmation and the user says yes, call "
    "claude_code_edit again. If they say no, call cancel_claude_edit.\n"
    "- Summarise the result in one or two sentences. Never read code aloud.\n\n"

    "TOOL USE:\n"
    "- When the user asks for a PC action, call the tool — do not describe it.\n"
    "- Never output JSON, tool schemas, or tool names as text.\n"
    "- Destructive actions (shutdown, delete, kill, empty bin) → ask confirm first.\n\n"

    "MEMORY (important):\n"
    "- If the user shares ANY personal detail (name, preference, project, location, "
    "likes/dislikes, birthday, email, ongoing work, family, habits), "
    "you MUST immediately call remember_fact(key, value, category).\n"
    "- Use categories: 'identity', 'preference', 'project', 'context'.\n"
    "- Never say 'I will remember that' — actually call remember_fact.\n\n"

    "EXTERNAL AI:\n"
    "- If the user says 'ask deepseek', 'ask chatgpt', 'ask claude', 'ask gemini', "
    "or 'check with another AI', call the matching tool with the user's question.\n"
    "- If the user asks you to compare AIs, call multiple ask_* tools sequentially.\n"
    "- Summarize the external AI's answer briefly — do not read it aloud verbatim.\n\n"

    "FORMATTING (strict):\n"
    "- When listing files, folders, processes, apps, or any set of items:\n"
    "  • Use a bullet character • followed by a space, one item per line.\n"
    "  • Never include file sizes, dates, or full paths unless asked.\n"
    "  • If more than 15 items, show the first 15 and add '… and N more'.\n"
    "  • Keep the intro and outro to ONE short sentence.\n"
    "  • Do NOT wrap the list in code blocks or quotes.\n"
    "- For normal conversation, use plain sentences — no bullets."
)

# ---------- TOOL CATEGORIES ----------
TOOL_CATEGORY = {
    "run_shell_command": "files", "read_file": "files", "write_file": "files",
    "list_dir": "files", "create_folder": "files", "move_path": "files",
    "copy_path": "files", "delete_path": "files", "find_files": "files",
    "open_path": "files", "grep_files": "files",
    "zip_path": "files", "unzip_path": "files",
    "get_time": "info", "system_info": "info", "battery_status": "info",
    "top_processes": "info",
    "lock_screen": "power", "sleep_pc": "power", "shutdown_pc": "power",
    "restart_pc": "power", "abort_shutdown": "power",
    "volume_up": "audio", "volume_down": "audio", "mute_toggle": "audio",
    "take_screenshot": "screen", "brightness_up": "screen",
    "brightness_down": "screen",
    "open_app": "apps", "close_app": "apps", "list_running_apps": "apps",
    "list_installed_apps": "apps", "kill_process": "apps",
    "show_desktop": "window", "switch_window": "window",
    "minimize_window": "window", "maximize_window": "window",
    "close_window": "window",
    "type_text": "input", "press_key": "input", "press_hotkey": "input",
    "move_mouse": "input", "click_mouse": "input", "scroll_screen": "input",
    "open_url": "web", "web_search": "web", "youtube_search": "web",
    "web_answer": "web", "search_sources": "web",
    "media_play_pause": "media", "media_next": "media",
    "media_previous": "media",
    "get_clipboard": "clipboard", "set_clipboard": "clipboard",
    "run_python": "python",
    "send_notification": "notify", "set_timer": "notify",
    "ping_host": "network", "get_ip": "network", "check_port": "network",
    "flush_dns": "network",
    "get_weather": "weather",
    "git_status": "dev", "docker_ps": "dev",
    "empty_recycle_bin": "recycle",
    "get_env": "env", "set_env": "env",
    "remember_fact": "memory", "forget_fact": "memory",
    "list_user_facts": "memory",
    "set_speech_pace": "speech", "get_speech_pace": "speech",
    "set_voice": "speech", "list_voices": "speech",
    "open_in_vscode": "devcode", "ask_claude_code": "devcode",
    "claude_code_edit": "devcode", "cancel_claude_edit": "devcode",
    "pending_edit": "devcode",
    "where_is": "files", "list_known_folders": "files",
    "ask_deepseek": "ai", "ask_chatgpt": "ai",
    "ask_claude": "ai", "ask_gemini": "ai",
    "ask_llama": "ai", "ask_qwen": "ai",
    "ask_local_model": "ai", "list_available_ais": "ai",
}

# Fail loudly if tools.py and this map drift apart. An uncategorised tool is
# invisible to the model forever; a phantom entry points at nothing.
_MISSING_CATEGORY = sorted(set(TOOL_REGISTRY) - set(TOOL_CATEGORY))
_PHANTOM_TOOLS = sorted(set(TOOL_CATEGORY) - set(TOOL_REGISTRY))
if _MISSING_CATEGORY:
    print(f"[brain] WARNING uncategorised tools (never offered to the model): "
          f"{_MISSING_CATEGORY}")
if _PHANTOM_TOOLS:
    print(f"[brain] WARNING categorised tools that do not exist in tools.py: "
          f"{_PHANTOM_TOOLS}")

CATEGORY_KEYWORDS = {
    "files": [
        "file", "folder", "directory", "list", "read", "write",
        "create folder", "make folder", "move ", "copy ", "rename",
        "delete", "find ", "search for", "shell", "command",
        "terminal", "run ", "execute", "cmd", "open file", "open folder",
        "zip", "unzip", "extract", "archive", "search inside", "grep",
        "desktop", "documents", "downloads",
        "where is", "what path", "full path", "which folder",
        "does not exist", "doesn't exist", "can't find", "cannot find",
    ],
    "info": [
        "time", "date", "today", "clock", "cpu", "ram", "memory",
        "disk", "storage", "battery", "system info", "how is my",
        "process", "processes", "task manager", "how much",
    ],
    "power": [
        "lock", "sleep", "shut down", "shutdown", "restart",
        "reboot", "abort", "power off",
    ],
    "audio": [
        "volume", "mute", "unmute", "louder", "quieter",
        "turn up", "turn down", "sound",
    ],
    "screen": [
        "screenshot", "screen shot", "screen capture",
        "capture screen", "capture the screen",
        "brightness", "dim", "brighter", "darker",
    ],
    "apps": [
        "open ", "close ", "launch", "running", "applications",
        "apps", "start ", "kill ", "installed", "kill process",
    ],
    "window": [
        "show desktop", "minimize", "maximize",
        "switch window", "alt tab", "close window", "close this window",
        "minimize this", "maximize this",
    ],
    "input": [
        "type ", "press ", "hotkey", "keyboard", "key combo",
        "ctrl+", "alt+", "shift+", "click", "move mouse",
        "scroll", "mouse",
    ],
    "web": [
        "search", "google", "browse", "website", "youtube", "url",
        "look up", "google for", "open website",
        "search the web", "find out", "latest news", "what's the latest",
        "whats the latest", "who is", "who was", "what year", "when did",
        "how tall", "how old", "capital of", "population of", "look it up",
    ],
    "media": [
        "play", "pause", "next track", "previous track", "skip",
        "song", "music",
    ],
    "clipboard": ["clipboard", "copy", "paste"],
    "python": ["python", "script", "code snippet", "run this code"],
    "notify": ["notify", "notification", "remind", "reminder", "timer",
               "set a timer", "countdown"],
    "network": ["ping", "ip address", "my ip", "port ", "dns",
                "network", "internet", "connection"],
    "weather": ["weather", "temperature", "forecast", "raining",
                "sunny", "how hot", "how cold"],
    "dev": ["git", "docker", "container", "repository", "repo",
            "commit", "branch"],
    "recycle": ["recycle bin", "trash", "empty the bin", "empty recycle"],
    "env": ["environment variable", "env var", "getenv", "setenv"],
    "memory": [
        "my name is", "call me", "i am ", "i'm ", "remember",
        "i like", "i love", "i hate", "i work", "i live",
        "my favorite", "my favourite", "my birthday", "my email",
        "my project", "i prefer", "don't forget", "dont forget",
        "note that", "keep in mind",
        "forget about", "forget that",
        "what do you know about me", "what do you remember",
        "list facts", "my facts",
    ],
    "speech": [
        "talk faster", "speak faster", "talk slower", "speak slower",
        "slow down", "speed up", "too fast", "too slow", "slower please",
        "faster please", "speaking pace", "speaking speed", "speech rate",
        "talk normally", "talk normal", "speak normally", "speak normal",
        "normal pace", "change voice", "switch voice", "different voice",
        "what voices", "list voices",
        # Bare "voice" is safe here: no other category uses the word, so any
        # mention of a voice should reach these tools.
        "voice",
    ],
    "devcode": [
        # "ask claude code" must outscore the "ask claude" entry below; the
        # length-based scoring in _score_categories handles that.
        "claude code", "ask claude code", "vs code", "vscode",
        "visual studio code", "code editor", "open the project",
        "open my project", "in the editor",
    ],
    "ai": [
        "ask deepseek", "ask chatgpt", "ask gpt", "ask claude",
        "ask gemini", "ask another ai", "ask another model",
        "ask qwen", "ask llama", "ask local model", "ask the local",
        "check with", "consult", "compare ai", "compare models",
        "which ai", "list ai", "what ais", "available ai",
    ],
}

_FILES_PHRASES = (
    "list my ", "list the ", "list ", "show me the files",
    "show files in", "show contents of", "what's in my",
    "whats in my", "what's on my", "whats on my",
    "contents of my", "open my ", "read my ",
)

_TOOL_JSON_PATTERNS = [
    # "claude_code_edit {"folder": "...", "request": "..."}" - the tool name sits
    # OUTSIDE the braces, so the name/parameters patterns below never matched it
    # and the raw call leaked into the spoken reply.
    re.compile(r'\b[a-z_]{3,40}\s*\{\s*"[^"]+"\s*:.*?\}', re.DOTALL),
    re.compile(r'\{\s*"name"\s*:\s*"[^"]*"\s*,\s*"parameters"\s*:\s*\{[^{}]*\}\s*\}'),
    re.compile(r'\{\s*"name"\s*:\s*"[^"]*"\s*\}'),
    re.compile(r'\{\s*"parameters"\s*:\s*\{[^{}]*\}\s*\}'),
    re.compile(r'<\|tool_call\|>.*?</\|tool_call\|>', re.DOTALL),
]

_FOREIGN_SCRIPTS = re.compile(
    r'[\u0E00-\u0E7F\u0900-\u097F\u0980-\u09FF'
    r'\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF'
    r'\u0600-\u06FF\u0590-\u05FF]+'
)


# ---------- ACTION-INTENT FALLBACK ----------
# Keyword gating alone means an unrecognised phrasing gets ZERO tools, so Jarvis
# talks about the action instead of doing it. When the message clearly reads as a
# command, hand over a small curated set of high-value tools instead of nothing.
# It stays small on purpose: dumping all 80 schemas is what made the 3b model
# hallucinate tool JSON.
CORE_ACTION_TOOLS = [
    "run_shell_command", "list_dir", "read_file", "write_file", "open_path",
    "find_files", "get_time", "system_info", "top_processes",
    "open_app", "close_app", "list_running_apps",
    "take_screenshot", "volume_up", "volume_down", "mute_toggle",
    "lock_screen", "show_desktop", "press_hotkey", "type_text",
    "open_url", "web_search", "get_clipboard", "set_clipboard",
    "send_notification", "remember_fact",
    "where_is", "list_known_folders", "open_in_vscode",
]

# Imperative verb at the start of the sentence = "do this to my PC".
_ACTION_LEAD = re.compile(
    r"^\s*(?:please\s+|jarvis[,\s]+|hey jarvis[,\s]+|can you\s+|could you\s+|"
    r"would you\s+|go ahead and\s+|i want you to\s+|i need you to\s+)*"
    r"(open|close|start|stop|launch|run|execute|show|list|read|write|create|make|"
    r"move|copy|rename|delete|remove|find|search|get|set|"
    r"turn|take|capture|type|press|click|scroll|play|pause|skip|mute|lock|sleep|"
    r"restart|shut|reboot|ping|flush|zip|unzip|extract|send|notify|remind|empty|"
    r"switch|minimize|maximize|screenshot|remember|forget|ask)\b",
    re.IGNORECASE)

# Phrases that mean "act on my machine" even without an imperative lead.
_ACTION_HINTS = (
    "my pc", "my computer", "my laptop", "my machine", "my desktop",
    "on my screen", "for me", "do it", "right now", "go ahead",
)

# Chat that happens to start with an action verb. These stay tool-free.
_SMALL_TALK = (
    "tell me a joke", "make me laugh", "tell me a story", "tell me about yourself",
    "who are you", "what can you do", "what are you", "how are you",
    "good morning", "good evening", "good night", "thank you", "thanks",
    "nice to meet you", "how's it going", "hows it going",
)

# Cap how many categories can fire at once. Unbounded unions were the cause of
# the "list my desktop" collision (files + window + apps all at once).
MAX_CATEGORIES = 3

# A category whose best keyword is this many characters shorter than the winner
# is treated as an incidental match and dropped.
SCORE_MARGIN = 4


def _edit_is_pending() -> bool:
    """True while a Claude Code edit is waiting for the user's yes or no."""
    return getattr(_tools, "_PENDING_EDIT", None) is not None


def _looks_like_command(t: str) -> bool:
    if _edit_is_pending():
        return True
    if any(p in t for p in _SMALL_TALK):
        return False
    if _ACTION_LEAD.match(t):
        return True
    return any(h in t for h in _ACTION_HINTS)


# Words that mean "put something on my screen" rather than "tell me the answer".
_SHOW_WORDS = ("open ", "show ", "browse", "browser", "tab", "youtube",
               "video", "play ", "pull up", "bring up", "go to ")

_QUESTION_LEAD = re.compile(
    r"^\s*(?:hey jarvis[,\s]+|jarvis[,\s]+)?"
    r"(who|what|when|where|which|whose|how many|how much|how tall|how old|"
    r"how far|how long|is there|are there|did |does |do )",
    re.IGNORECASE)

_LOOKUP_PHRASES = ("look up", "look it up", "search the web", "find out",
                   "latest news", "what's the latest", "whats the latest")


def _is_world_question(t: str) -> bool:
    """A question expecting a SPOKEN answer, not a browser tab."""
    if any(w in t for w in _SHOW_WORDS):
        return False
    return bool(_QUESTION_LEAD.match(t)) or any(p in t for p in _LOOKUP_PHRASES)


# Web tools that speak their answer, as opposed to opening something.
_SPOKEN_WEB = ("web_answer", "search_sources")


def _schemas_for(cats, spoken_web_only=False):
    out = [s for s in TOOL_SCHEMAS
           if TOOL_CATEGORY.get(s["function"]["name"]) in cats]
    if spoken_web_only:
        out = [s for s in out
               if TOOL_CATEGORY.get(s["function"]["name"]) != "web"
               or s["function"]["name"] in _SPOKEN_WEB]
    return out


def _score_categories(t: str) -> dict:
    """Rank categories by the longest keyword each one matched.

    A long, specific match ("show desktop") should beat an incidental short one
    ("show "), so the strongest few categories win instead of every category
    that happened to contain a common word.
    """
    scores = {}
    for cat, kws in CATEGORY_KEYWORDS.items():
        hits = [k for k in kws if k in t]
        if hits:
            scores[cat] = max(len(k.strip()) for k in hits)
    return scores


def relevant_schemas(user_text: str):
    t = user_text.lower()
    scores = _score_categories(t)

    # "ask claude code" also matches the ai category's "ask claude" keyword, and
    # ai is pinned past the score margin -- so ask_claude (a LOCAL Ollama model)
    # was being offered next to ask_claude_code. Drop the ai category outright
    # when the user clearly means Claude Code.
    if "claude code" in t:
        scores.pop("ai", None)

    # "open VS Code on ..." matches apps ("open "), files ("folder") and devcode
    # at once, putting open_app, open_path and open_in_vscode all in scope. The
    # 7b model then picked open_path and opened Explorer while claiming it had
    # opened VS Code. When the editor is named, it is the only opener that makes
    # sense, so drop the rivals.
    if any(k in t for k in ("vs code", "vscode", "visual studio code")):
        scores.pop("apps", None)
        scores.pop("files", None)
        scores.pop("window", None)

    # A question with no local match at all is a question about the world, so
    # offer the lookup tool rather than nothing. Deliberately ONLY when nothing
    # else scored: boosting web whenever a question appeared crushed the local
    # categories, and "what time is it" lost get_time to web_answer.
    #
    # The narrowing to a single web tool is handled separately, by
    # spoken_web_only below, so no score boost is needed for that.
    if _is_world_question(t) and not scores:
        scores["web"] = 40

    # While an edit is armed, the next message is usually a bare "yes" or "no",
    # which matches no keyword at all -- so devcode fell out of scope and the
    # model wandered off into list_dir. Keep it available until resolved.
    if _edit_is_pending():
        scores["devcode"] = max(scores.get("devcode", 0), 99)

    memory_hit = "memory" in scores
    ai_hit = "ai" in scores

    # "list my X" / "what's in my X" usually means the filesystem, so nudge
    # files just past whatever else matched -- but only just. A hard override
    # here sent "what's in my clipboard" to the file tools and left
    # get_clipboard unreachable.
    if any(p in t for p in _FILES_PHRASES):
        rivals = [v for c, v in scores.items()
                  if c not in ("files", "memory", "ai")]
        scores["files"] = max([scores.get("files", 0)] + rivals) + 1

    if scores:
        # memory and ai are explicit-intent categories — never drop them to the cap.
        pinned = {c for c in ("memory", "ai") if c in scores}
        free = {c: v for c, v in scores.items() if c not in pinned}
        if free:
            top = max(free.values())
            strong = [c for c, v in free.items() if v >= top - SCORE_MARGIN]
            strong.sort(key=lambda c: -free[c])
            cats = pinned | set(strong[:max(1, MAX_CATEGORIES - len(pinned))])
        else:
            cats = pinned
        return _schemas_for(cats, spoken_web_only=_is_world_question(t))

    # Nothing matched. If it still reads as a command, offer the core set rather
    # than leaving Jarvis with no way to act.
    if _looks_like_command(t):
        return [s for s in TOOL_SCHEMAS
                if s["function"]["name"] in CORE_ACTION_TOOLS]

    # Genuine small talk ("hello", "how are you") — no tools, as intended.
    return []


# ---------- DETERMINISTIC PRE-ROUTER ----------
# Measured: qwen2.5:7b answered 0 of 4 world questions by calling web_answer.
# It answered from stale memory instead AND invented citations - "according to
# Wikipedia", "according to NASA" - for lookups it never performed. Prompt
# rules did not change that.
#
# So for the one case where gating has already concluded there is exactly one
# applicable tool, the tool is called here and the result handed to the model.
# The model's only job left is to say it. It cannot skip the lookup, because the
# lookup already happened.
#
# Scope is deliberately narrow: ONLY when the offered schemas are exactly the
# spoken-web tools. If anything local also applies, the model still decides.
def forced_lookup(user_text: str, schemas):
    names = {s["function"]["name"] for s in schemas}
    if "web_answer" not in names or not names <= set(_SPOKEN_WEB):
        return None
    if not _is_world_question((user_text or "").lower()):
        return None
    try:
        return TOOL_REGISTRY["web_answer"](query=user_text)
    except Exception as e:
        return "The web lookup failed: %s" % e


LOOKUP_FRAME = (
    "A web lookup was just performed for the user's question. Answer ONLY from "
    "the text below and name its source. Do not add facts from memory. If it "
    "says no direct answer was found, tell the user that.\n\nLOOKUP RESULT:\n"
)


def strip_thinking(text: str) -> str:
    return re.sub(r" thinking.*?", "", text, flags=re.DOTALL).strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def clean_hallucination(text: str) -> str:
    text = _FOREIGN_SCRIPTS.sub('', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_tool_json(text: str) -> str:
    for pat in _TOOL_JSON_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"^\s*[\{\[].*?[\}\]]\s*$", "", text, flags=re.DOTALL)
    return clean_hallucination(text)


# ---------- ANTI-REPETITION ----------
def _normalize(s: str) -> str:
    return re.sub(r'\W+', ' ', s.lower()).strip()


def dedupe_reply(text: str, user_input: str = "") -> str:
    """
    Remove consecutive duplicate sentences and drop echo of the user's question.
    """
    if not text or len(text) < 12:
        return text

    # Split into sentences and also respect newlines
    raw = re.split(r'(?<=[.!?])\s+|\n+', text)
    sentences = [s.strip() for s in raw if s.strip()]

    user_norm = _normalize(user_input) if user_input else ""
    out = []
    seen = set()
    prev_norm = None

    for s in sentences:
        norm = _normalize(s)
        if not norm:
            continue

        # Drop exact consecutive duplicates
        if norm == prev_norm:
            continue

        # Drop the sentence if it just echoes the user's question
        if user_norm and (norm == user_norm or
                          (len(norm) > 12 and norm in user_norm) or
                          (len(user_norm) > 12 and user_norm in norm and len(norm) - len(user_norm) < 10)):
            prev_norm = norm
            continue

        # Drop if the same normalized sentence appeared earlier in this reply
        if norm in seen and len(norm) > 15:
            continue

        seen.add(norm)
        out.append(s)
        prev_norm = norm

    result = ' '.join(out).strip()
    return result if result else text


class JarvisBrain:
    def __init__(self, model: str = MODEL):
        self.model = model

    def new_conversation(self, user_facts: str = "") -> list[dict]:
        sys = SYSTEM_PROMPT
        if user_facts:
            sys += f"\n\nWHAT YOU KNOW ABOUT THE USER:\n{user_facts}"
        return [{"role": "system", "content": sys}]

    def _last_user_message(self, messages):
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""

    def _run_stream(self, messages, schemas):
        stream = ollama.chat(
            model=self.model,
            messages=messages,
            tools=schemas if schemas else None,
            stream=True,
            options={
                "num_predict": 220,
                "temperature": 0.35,
                "top_p": 0.85,
                "repeat_penalty": 1.15,
                "num_ctx": NUM_CTX,
            },
            keep_alive="30m",
        )
        content = ""
        tool_calls = None
        for chunk in stream:
            msg = chunk.message
            if msg.content:
                content += msg.content
                yield ("token", msg.content)
            if getattr(msg, "tool_calls", None):
                tool_calls = msg.tool_calls
        yield ("__done__", (content, tool_calls))

    def stream_with_tools(self, messages: list[dict], max_tool_rounds: int = 4):
        user_text = self._last_user_message(messages)
        # Marks the turn boundary for tools.py's two-turn confirmation gate.
        try:
            TOOL_REGISTRY and begin_turn(user_text)
        except Exception:
            pass
        schemas = relevant_schemas(user_text)
        rounds = 0

        # Pre-router: perform the lookup before generating, when it is the only
        # thing that could apply. Kept OUT of `messages` so the conversation
        # history is not permanently padded with search text - NUM_CTX is 2048.
        lookup_note = None
        if schemas:
            result = forced_lookup(user_text, schemas)
            if result:
                yield ("tool", "web_answer")
                schemas = []
                lookup_note = {"role": "system",
                               "content": LOOKUP_FRAME + str(result)}

        while rounds <= max_tool_rounds:
            use_schemas = schemas if rounds < max_tool_rounds else []
            content = ""
            tool_calls = None
            gen = (messages + [lookup_note]) if lookup_note else messages

            for evt, payload in self._run_stream(gen, use_schemas):
                if evt == "token":
                    yield ("token", payload)
                elif evt == "__done__":
                    content, tool_calls = payload

            cleaned = clean_tool_json(content)

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": cleaned,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    fn_name = tc.function.name
                    args = tc.function.arguments or {}
                    yield ("tool", fn_name)
                    try:
                        if fn_name not in TOOL_REGISTRY:
                            result = f"Unknown tool: {fn_name}"
                        else:
                            result = TOOL_REGISTRY[fn_name](**args)
                    except Exception as e:
                        result = f"Tool error: {e}"
                    messages.append({"role": "tool", "content": str(result)})
                rounds += 1
                continue

            if not cleaned.strip():
                retry_msgs = (messages + ([lookup_note] if lookup_note else []) + [
                    {"role": "assistant", "content": "(invalid response discarded)"},
                    {"role": "user", "content": "Reply in plain English only."},
                ])
                retry = ""
                for evt, payload in self._run_stream(retry_msgs, []):
                    if evt == "token":
                        retry += payload
                        yield ("token", payload)
                    elif evt == "__done__":
                        retry, _ = payload
                retry_clean = clean_tool_json(retry).strip() or \
                              "Sorry, I didn't quite catch that, sir."
                retry_clean = dedupe_reply(retry_clean, user_text)
                messages.append({"role": "assistant", "content": retry_clean})
                yield ("done", retry_clean)
                return

            final_text = dedupe_reply(cleaned, user_text)
            messages.append({"role": "assistant", "content": final_text})
            yield ("done", final_text)
            return

        final = ""
        for evt, payload in self._run_stream(messages, []):
            if evt == "token":
                final += payload
                yield ("token", payload)
            elif evt == "__done__":
                final, _ = payload
        final_clean = clean_tool_json(final).strip() or \
                      "Sorry, I didn't quite catch that, sir."
        final_clean = dedupe_reply(final_clean, user_text)
        messages.append({"role": "assistant", "content": final_clean})
        yield ("done", final_clean)

    def chat(self, user_input: str) -> str:
        msgs = self.new_conversation()
        msgs.append({"role": "user", "content": user_input})
        out = ""
        for evt, payload in self.stream_with_tools(msgs):
            if evt == "token":
                out += payload
        return clean_tool_json(out)

    def reset(self):
        pass