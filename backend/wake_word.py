import json, pyaudio
from vosk import Model, KaldiRecognizer

def wait_for_wake_word(model_path="models/vosk", keyword="jarvis"):
    model = Model(model_path)
    rec = KaldiRecognizer(model, 16000, f'["{keyword}", "[unk]"]')
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                    input=True, frames_per_buffer=8000)
    stream.start_stream()
    while True:
        data = stream.read(4000, exception_on_overflow=False)
        if rec.AcceptWaveform(data):
            if keyword in json.loads(rec.Result()).get("text", ""):
                stream.stop_stream(); stream.close(); p.terminate()
                return True