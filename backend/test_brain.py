from brain import JarvisBrain

brain = JarvisBrain()
for token in brain.chat_stream("Introduce yourself as JARVIS in 2 sentences."):
    print(token, end="", flush=True)