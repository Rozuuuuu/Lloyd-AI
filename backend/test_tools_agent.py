# test_tools_agent.py
from brain_with_tools import chat_with_tools
messages = [{"role": "system", "content": "You are JARVIS."}]
print(chat_with_tools("What files are in the current directory?", messages))