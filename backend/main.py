from brain import JarvisBrain
from memory import JarvisMemory
from stt import listen_once
from tts import JarvisVoice
from brain import strip_thinking
from wake_word import wait_for_wake_word

def main():
    brain = JarvisBrain()
    mem = JarvisMemory()
    voice = JarvisVoice(r"C:\Users\Home\Desktop\AI\Jarvis AI\models\piper\en_GB-alan-medium.onnx",
    r"C:\Users\Home\Desktop\AI\Jarvis AI\models\piper\en_GB-alan-medium.onnx.json")

    for m in mem.get_recent_context(8):
        brain.conversation.append(m)
    facts = mem.facts_summary()
    if facts:
        brain.conversation[0]["content"] += f"\n\nKnown facts:\n{facts}"

    print("JARVIS online. Say 'jarvis' to wake.")
    while True:
        wait_for_wake_word(model_path=r"C:\Users\Home\Desktop\AI\Jarvis AI\models\vosk")
        voice.speak("Yes sir?")
        user = listen_once()
        if not user:
            continue
        print(f"You: {user}")
        mem.save_message("user", user)

        print("JARVIS: ", end="", flush=True)
        full = ""
        for tok in brain.chat_stream(user):
            print(tok, end="", flush=True)
            full += tok
        print()
        mem.save_message("assistant", full)

        spoken = strip_thinking(full)
        if spoken:
            voice.speak(spoken)

if __name__ == "__main__":
    main()