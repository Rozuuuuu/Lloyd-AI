from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from brain import (JarvisBrain, strip_markdown, clean_tool_json,
                   clean_hallucination, dedupe_reply)
from memory import JarvisMemory
from tools import register_memory, register_speech
from speech import controller as speech
import ollama, json, base64, wave, os, sys, tempfile, uuid, re, asyncio, time
from concurrent.futures import ThreadPoolExecutor

# ---------- CONFIG ----------
MODEL_NAME = "qwen2.5:7b"
# Voices are discovered from models/piper by speech.py; no hardcoded paths.

NUM_CTX       = 2048
HISTORY_TURNS = 5
TOKEN_BATCH   = 4
# ----------------------------

# ---------- SAFE CONSOLE LOGGING ----------
# The Windows console is cp1252 by default. A filename, app name, or spoken
# phrase containing anything outside that codepage makes print() raise
# UnicodeEncodeError, which lands in the WebSocket handler's except clause
# and kills the session. Logging must never break a command.
def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

brain = JarvisBrain(model=MODEL_NAME)
mem = JarvisMemory()
register_memory(mem)
register_speech(speech)
executor = ThreadPoolExecutor(max_workers=4)

try:
    ollama.generate(model=MODEL_NAME, prompt="hi", keep_alive="30m",
                    options={"num_ctx": NUM_CTX})
    log(f"[warmup] {MODEL_NAME} loaded")
except Exception as e:
    log(f"[warmup] skipped: {e}")

# Load the Piper model up front. The first synthesis takes seconds while the
# model loads, which would otherwise land in the middle of the first reply.
log(f"[warmup] voices: {speech.voice_names()}")
log(f"[warmup] {speech.warmup()}")


# ---------- LIST DETECTION (skip reading lists aloud) ----------
_LIST_LINE = re.compile(r'^\s*(?:[•·▪●◦‣*\-–—]|\d+[.)])\s+')

def is_list_item(line: str) -> bool:
    """Return True if this line looks like a list entry."""
    s = line.strip()
    if not s:
        return True
    if _LIST_LINE.match(s):
        return True
    # Heuristic: long line with no sentence-ending punctuation
    if len(s) > 120 and not any(c in s for c in ".!?;:"):
        return True
    # Filename-ish: "something.ext" with no spaces
    if re.match(r'^[\w\-\. ]+\.\w{1,5}$', s) and ' ' not in s:
        return True
    return False


def extract_speakable(text: str) -> tuple[str, str]:
    """
    Split text into (speakable_text, remaining_buffer).
    Complete lines separated by \n are checked for list-ness and filtered.
    Returns speakable prose plus an unfinished tail.
    """
    if '\n' not in text:
        return "", text

    idx = text.rfind('\n')
    complete = text[:idx]
    remaining = text[idx + 1:]

    speakable_lines = []
    for line in complete.split('\n'):
        if not is_list_item(line):
            speakable_lines.append(line)
    return '\n'.join(speakable_lines), remaining


SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')


def synthesize_b64(text: str) -> str:
    """Synthesize one span of speech, honouring the live voice and rate.

    Builds the WAV in memory. The old version wrote and deleted a temp file per
    sentence, which is pure latency on the path that is meant to sound
    continuous.
    """
    if not text.strip():
        return ""
    try:
        wav = speech.synthesize_wav(text)
    except Exception as e:
        log(f"[tts] {e}")
        return ""
    return base64.b64encode(wav).decode() if wav else ""


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    await ws.accept()
    session_id = str(uuid.uuid4())[:8]
    log(f"[session {session_id}] connected")

    await ws.send_json({"type": "speech_config", **speech.config()})

    user_facts = mem.facts_summary()
    conversation = brain.new_conversation(user_facts=user_facts)
    for m in mem.get_recent_context(session_id, limit=HISTORY_TURNS * 2):
        conversation.append(m)

    loop = asyncio.get_event_loop()

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            mtype = msg.get("type")

            # Voice and pace are changed over the open socket rather than a
            # REST call, so the phone never has to reconnect to retune speech.
            if mtype == "set_rate":
                log(f"[session {session_id}] rate -> {msg.get('value')}")
                speech.set_rate(msg.get("value", 1.0))
                await ws.send_json({"type": "speech_config", **speech.config()})
                continue
            if mtype == "set_voice":
                result = speech.set_voice(msg.get("name", ""))
                log(f"[session {session_id}] voice -> {result}")
                await ws.send_json({"type": "speech_config", **speech.config()})
                continue
            if mtype == "get_speech_config":
                await ws.send_json({"type": "speech_config", **speech.config()})
                continue

            if mtype != "user_message":
                continue

            user_text = msg["content"].strip()
            if not user_text:
                continue

            t0 = time.time()
            log(f"[session {session_id}] user: {user_text}")

            mem.save_message(session_id, "user", user_text)
            conversation.append({"role": "user", "content": user_text})

            # trim history
            if len(conversation) > 1 + HISTORY_TURNS * 2:
                system = conversation[0]
                conversation = [system] + conversation[-HISTORY_TURNS * 2:]

            await ws.send_json({"type": "state", "value": "thinking"})

            full_reply = ""
            speak_buffer = ""
            token_batch = []
            first_token_sent = False
            speaking_started = False
            memory_updated = False

            for event_type, payload in brain.stream_with_tools(conversation):
                if event_type == "tool":
                    log(f"[session {session_id}] tool: {payload} (+{time.time()-t0:.1f}s)")
                    if payload in ("remember_fact", "forget_fact"):
                        memory_updated = True
                    await ws.send_json({"type": "tool", "name": payload})
                    continue

                if event_type == "token":
                    full_reply += payload
                    speak_buffer += payload
                    token_batch.append(payload)

                    if len(token_batch) >= TOKEN_BATCH:
                        if not first_token_sent:
                            log(f"[session {session_id}] first token +{time.time()-t0:.2f}s")
                            first_token_sent = True
                        await ws.send_json({"type": "token", "content": "".join(token_batch)})
                        token_batch = []

                    # Extract speakable prose from complete lines
                    speakable, speak_buffer = extract_speakable(speak_buffer)
                    if speakable:
                        for sentence in SENT_SPLIT.split(speakable):
                            clean = strip_markdown(sentence).strip()
                            if len(clean) < 3:
                                continue
                            if not speaking_started:
                                speaking_started = True
                                await ws.send_json({"type": "state", "value": "speaking"})
                            audio = await loop.run_in_executor(
                                executor, synthesize_b64, clean)
                            if audio:
                                await ws.send_json({"type": "audio", "content": audio})
                    continue

                if event_type == "done":
                    if token_batch:
                        await ws.send_json({"type": "token", "content": "".join(token_batch)})
                        token_batch = []

                    # Flush any remaining speakable tail
                    if speak_buffer.strip():
                        for sentence in SENT_SPLIT.split(speak_buffer):
                            if is_list_item(sentence):
                                continue
                            clean = strip_markdown(sentence).strip()
                            if len(clean) >= 3:
                                if not speaking_started:
                                    speaking_started = True
                                    await ws.send_json({"type": "state", "value": "speaking"})
                                audio = await loop.run_in_executor(
                                    executor, synthesize_b64, clean)
                                if audio:
                                    await ws.send_json({"type": "audio", "content": audio})

                    # full_reply is the raw token stream, so it has NOT been
                    # through dedupe_reply the way brain's "done" payload has.
                    # Without this, the reply sent to the phone still contains
                    # the duplicated sentences dedupe_reply exists to remove.
                    cleaned_final = dedupe_reply(
                        clean_hallucination(clean_tool_json(full_reply)).strip(),
                        user_text)
                    if not cleaned_final:
                        cleaned_final = "Sorry, I didn't quite catch that, sir."
                        await ws.send_json({"type": "token", "content": cleaned_final})

                    await ws.send_json({"type": "replace", "content": cleaned_final})
                    mem.save_message(session_id, "assistant", cleaned_final)
                    # brain.stream_with_tools already appended the assistant turn
                    # to this same list. Appending again duplicated every reply in
                    # the history, burning half of a 2048-token context window on
                    # repeats - and feeding the model its own words twice, which
                    # is the opposite of what the anti-repetition work wanted.
                    # Overwrite that entry with the cleaned text instead.
                    if conversation and conversation[-1].get("role") == "assistant":
                        conversation[-1]["content"] = cleaned_final
                    else:
                        conversation.append({"role": "assistant",
                                             "content": cleaned_final})

                    # If memory was updated, refresh the system prompt
                    if memory_updated:
                        new_facts = mem.facts_summary()
                        conversation[0]["content"] = brain.new_conversation(
                            user_facts=new_facts)[0]["content"]

                    log(f"[session {session_id}] complete +{time.time()-t0:.2f}s")
                    await ws.send_json({"type": "state", "value": "idle"})

    except WebSocketDisconnect:
        log(f"[session {session_id}] disconnected")
    except Exception as e:
        log(f"[session {session_id}] error: {e}")
        try:
            await ws.send_json({"type": "state", "value": "idle"})
        except Exception:
            pass


@app.get("/")
async def index():
    return FileResponse("mobile.html")


@app.get("/health")
async def health():
    return {"status": "online", "model": brain.model}


@app.post("/model")
async def set_model(body: dict):
    global brain
    brain = JarvisBrain(model=body["name"])
    try:
        ollama.generate(model=body["name"], prompt="hi",
                        keep_alive="30m", options={"num_ctx": NUM_CTX})
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "model": body["name"]}