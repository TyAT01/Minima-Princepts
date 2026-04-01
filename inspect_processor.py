import torch
from transformers import AutoProcessor
import inspect

model_id = "FlashLabs/Chroma-4B"
try:
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    print(f"Processor type: {type(processor)}")
    print(f"Call signature: {inspect.signature(processor.__call__)}")
except Exception as e:
    print(f"Error: {e}")
