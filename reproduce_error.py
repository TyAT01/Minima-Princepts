import sys
import os
import torch
from transformers import AutoProcessor

# Add Aurelia_chroma to path
sys.path.append(os.path.abspath("Aurelia_chroma"))

model_id = "FlashLabs/Chroma-4B"
try:
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    print("Processor loaded successfully.")

    # Try calling it with the current code's style
    conversation = [[
        {"role": "system", "content": [{"type": "text", "text": "test"}]},
        {"role": "user", "content": [{"type": "text", "text": "test"}]}
    ]]

    try:
        print("Testing with prompt_audio and conversations...")
        inputs = processor(prompt_audio=None, conversations=conversation, add_generation_prompt=True, return_tensors="pt")
        print("Success with prompt_audio and conversations!")
    except Exception as e:
        print(f"Error with prompt_audio and conversations: {e}")

except Exception as e:
    print(f"Failed to load processor: {e}")
