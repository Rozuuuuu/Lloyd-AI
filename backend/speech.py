"""
JARVIS speech synthesis: voice selection and speaking pace.

Split out of server.py so tools.py can change the voice or the pace at runtime
without importing the web server, which would be circular.

Pace is exposed as RATE, where higher means faster, because that is what a
person means by "talk faster". Piper's own knob is length_scale, where higher
means SLOWER, so the two are reciprocals and the conversion lives here in one
place rather than being re-derived at every call site.
"""
import os
import threading
import wave
from io import BytesIO

from piper import PiperVoice

try:
    from piper.config import SynthesisConfig
except Exception:          # older piper without a config object
    SynthesisConfig = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(PROJECT_ROOT, "models", "piper")

# Rate limits. Below ~0.6 Piper slurs; above ~1.7 it is hard to follow.
RATE_MIN, RATE_MAX = 0.6, 1.7
RATE_STEP = 0.15
DEFAULT_RATE = 1.0
DEFAULT_VOICE = "alan"


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class SpeechController:
    """Owns the loaded Piper voices, the active voice, and the speaking rate."""

    def __init__(self, models_dir: str = MODELS_DIR):
        self.models_dir = models_dir
        self.rate = DEFAULT_RATE
        self._lock = threading.Lock()
        self._loaded = {}                 # short name -> PiperVoice
        self.voices = self._discover()    # short name -> .onnx path
        self.voice_name = (DEFAULT_VOICE if DEFAULT_VOICE in self.voices
                           else (next(iter(self.voices)) if self.voices else ""))

    # ---------- discovery ----------
    def _discover(self) -> dict:
        """Map a short spoken name to each .onnx in models/piper.

        'en_GB-alan-medium.onnx' -> 'alan', so the user can just say "alan".
        """
        found = {}
        if not os.path.isdir(self.models_dir):
            return found
        for fn in sorted(os.listdir(self.models_dir)):
            if not fn.endswith(".onnx"):
                continue
            stem = fn[:-5]
            parts = stem.split("-")
            short = parts[1] if len(parts) >= 2 else stem
            found[short.lower()] = os.path.join(self.models_dir, fn)
            found[stem.lower()] = os.path.join(self.models_dir, fn)
        return found

    def voice_names(self) -> list:
        """The short, speakable names only (no duplicate long form)."""
        seen, out = set(), []
        for name, path in self.voices.items():
            if path in seen or "-" in name:
                continue
            seen.add(path)
            out.append(name)
        return sorted(out)

    # ---------- loading ----------
    def _voice(self, name: str = ""):
        name = (name or self.voice_name).lower()
        path = self.voices.get(name)
        if not path:
            raise ValueError("No voice named '%s'. Available: %s"
                             % (name, ", ".join(self.voice_names())))
        with self._lock:
            if name not in self._loaded:
                cfg = path + ".json"
                self._loaded[name] = PiperVoice.load(path, cfg)
            return self._loaded[name]

    def warmup(self) -> str:
        try:
            self.synthesize_wav("Systems online.")
            return "voice '%s' ready" % self.voice_name
        except Exception as e:
            return "voice warmup failed: %s" % e

    # ---------- settings ----------
    def set_voice(self, name: str) -> str:
        key = (name or "").strip().lower()
        if key not in self.voices:
            return ("No voice named '%s'. Available: %s"
                    % (name, ", ".join(self.voice_names())))
        self._voice(key)                 # load before switching, so a bad model
        self.voice_name = key            # cannot leave us with no voice at all
        return "Voice set to %s." % key

    def set_rate(self, rate: float) -> str:
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return "Speaking rate must be a number, for example 1.2."
        self.rate = _clamp(rate, RATE_MIN, RATE_MAX)
        return self.describe_rate()

    def nudge_rate(self, direction: str) -> str:
        d = (direction or "").strip().lower()
        if d in ("faster", "quicker", "speed up", "up", "+"):
            self.rate = _clamp(self.rate + RATE_STEP, RATE_MIN, RATE_MAX)
        elif d in ("slower", "slow down", "down", "-"):
            self.rate = _clamp(self.rate - RATE_STEP, RATE_MIN, RATE_MAX)
        elif d in ("normal", "default", "reset"):
            self.rate = DEFAULT_RATE
        else:
            return "Say faster, slower, or normal. %s" % self.describe_rate()
        return self.describe_rate()

    def describe_rate(self) -> str:
        if abs(self.rate - 1.0) < 0.02:
            return "Speaking at normal pace."
        pct = int(round((self.rate - 1.0) * 100))
        if self.rate >= RATE_MAX - 0.01:
            return "Speaking as fast as I can, %d percent above normal." % pct
        if self.rate <= RATE_MIN + 0.01:
            return "Speaking as slowly as I can, %d percent below normal." % abs(pct)
        return ("Speaking %d percent %s than normal."
                % (abs(pct), "faster" if pct > 0 else "slower"))

    def config(self) -> dict:
        return {
            "voice": self.voice_name,
            "voices": self.voice_names(),
            "rate": round(self.rate, 3),
            "rate_min": RATE_MIN,
            "rate_max": RATE_MAX,
        }

    # ---------- synthesis ----------
    def _syn_config(self):
        if SynthesisConfig is None:
            return None
        # length_scale is the reciprocal of rate: higher scale = slower speech.
        return SynthesisConfig(length_scale=1.0 / max(0.01, self.rate))

    def synthesize_wav(self, text: str) -> bytes:
        """Return a complete WAV for `text`, honouring the current voice+rate.

        Written to an in-memory buffer rather than a temp file: the old code
        wrote and deleted a file per sentence, which is pure latency on the
        path that is supposed to feel continuous.
        """
        if not text or not text.strip():
            return b""
        voice = self._voice()
        syn = self._syn_config()
        buf = BytesIO()
        with wave.open(buf, "wb") as w:
            if syn is not None:
                try:
                    voice.synthesize_wav(text, w, syn_config=syn)
                except TypeError:
                    voice.synthesize_wav(text, w)
            else:
                voice.synthesize_wav(text, w)
        return buf.getvalue()


# A single shared controller. server.py synthesizes through it and tools.py
# adjusts it, so both see the same live settings.
controller = SpeechController()
