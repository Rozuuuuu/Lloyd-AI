import wave, soundfile as sf, sounddevice as sd
from piper import PiperVoice

class JarvisVoice:
    def __init__(self, model_path, config_path):
        self.voice = PiperVoice.load(model_path, config_path)

    def speak(self, text: str):
        if not text.strip():
            return
        with wave.open("data/_speak.wav", "wb") as w:
            self.voice.synthesize(text, w)
        data, sr = sf.read("data/_speak.wav")
        sd.play(data, sr)
        sd.wait()