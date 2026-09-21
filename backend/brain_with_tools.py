# brain_with_tools.py
import ollama
from tools import TOOL_REGISTRY

def chat_with_tools(user_input: str, messages: list) -> str:
    messages.append({"role": "user", "content": user_input})

    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "Run a shell command on the host machine",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The command to run"}
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file's contents",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"],
                },
            },
        },
    ]

    response = ollama.chat(
        model="deepseek-r1:8b",
        messages=messages,
        tools=tools,
    )
    
    # Agent loop: keep calling tools until the model is done
    while response.message.tool_calls:
        for tool_call in response.message.tool_calls:
            fn_name = tool_call.function.name
            args = tool_call.function.arguments
            result = TOOL_REGISTRY[fn_name](**args)

            messages.append(response.message)
            messages.append({
                "role": "tool",
                "content": str(result),
                "name": fn_name,
            })

        response = ollama.chat(
            model="deepseek-r1:8b",
            messages=messages,
            tools=tools,
        )

    messages.append(response.message)
    return response.message.content

BLOCKED_COMMANDS = ["rm -rf /", "mkfs", "dd if=", ":(){", "> /dev/sda"]

def safe_shell(command: str) -> str:
    for blocked in BLOCKED_COMMANDS:
        if blocked in command:
            return f"BLOCKED: '{command}' is a destructive operation."
    return run_shell_command(command)