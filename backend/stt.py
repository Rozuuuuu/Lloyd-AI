import speech_recognition as sr

def listen_once(timeout=6, phrase_limit=12) -> str | None:
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.4)
        try:
            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            return r.recognize_google(audio)
        except (sr.WaitTimeoutError, sr.UnknownValueError, sr.RequestError):
            return None