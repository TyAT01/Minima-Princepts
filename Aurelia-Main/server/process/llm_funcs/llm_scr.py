# OpenAI tool calling with history
### Uses a sample function
import yaml
import gradio as gr
import json
import os
from pathlib import Path
from openai import OpenAI

# Get the absolute path to the root directory (Aurelia-Main)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = ROOT_DIR / 'character_config.yaml'

with open(CONFIG_PATH, 'r') as f:
    char_config = yaml.safe_load(f)

# Allow for local API base URL
base_url = char_config.get('OPENAI_BASE_URL', None)
# Handle placeholder if still present (optional, but good for robustness)
if base_url and "<PORT>" in base_url:
    print(f"Warning: OPENAI_BASE_URL contains placeholder: {base_url}")

client = OpenAI(api_key=char_config['OPENAI_API_KEY'], base_url=base_url)

# Constants
HISTORY_FILE = char_config['history_file']
MODEL = char_config['model']

# Standard OpenAI format for system prompt
SYSTEM_PROMPT = [
    {
        "role": "system",
        "content": char_config['presets']['default']['system_prompt']
    }
]

# Load/save chat history
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return SYSTEM_PROMPT

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)



def get_riko_response_no_tool(messages):
    # Standard OpenAI Chat Completion call
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=1,
        top_p=1,
        max_tokens=2048,
        stream=False,
    )
    return response


def llm_response(user_input):

    messages = load_history()

    # Standard OpenAI format for user message
    messages.append({
        "role": "user",
        "content": user_input
    })


    response = get_riko_response_no_tool(messages)
    assistant_message = response.choices[0].message.content


    # Append assistant message to history
    messages.append({
        "role": "assistant",
        "content": assistant_message
    })

    save_history(messages)
    return assistant_message


if __name__ == "__main__":
    print('running main')
