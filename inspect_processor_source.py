import torch
from transformers import AutoProcessor
import inspect
import sys

model_id = "FlashLabs/Chroma-4B"
try:
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    # Print the source code of __call__ if it's a remote code
    print(f"Processor class: {processor.__class__}")
    print("--- Source Code of __call__ ---")
    try:
        print(inspect.getsource(processor.__call__))
    except Exception as e:
        print(f"Could not get source: {e}")
except Exception as e:
    print(f"Error: {e}")
