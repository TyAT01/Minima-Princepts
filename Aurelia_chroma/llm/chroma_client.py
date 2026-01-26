from __future__ import annotations
import logging
import torch
from transformers import AutoModelForCausalLM, AutoProcessor
import numpy as np

logger = logging.getLogger(__name__)

class ChromaClient:
    """A client for the FlashLabs Chroma 1.0 model."""

    def __init__(self, model_id: str = "FlashLabs/Chroma-4B", persona_prompt: str = "", max_new_tokens: int = 100):
        self._model_id = model_id
        self._persona_prompt = persona_prompt
        self._max_new_tokens = max_new_tokens
        self._model = None
        self._processor = None
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

    def load(self):
        """Loads the model and processor."""
        logger.info("Loading Chroma 1.0 model: %s", self._model_id)
        self._model = AutoModelForCausalLM.from_pretrained(
            self._model_id,
            trust_remote_code=True,
            device_map="auto",
            torch_dtype=torch.bfloat16
        )
        self._processor = AutoProcessor.from_pretrained(self._model_id, trust_remote_code=True)
        logger.info("Chroma 1.0 model loaded successfully.")

    def respond_to_audio(self, audio_path: str, context: str = "") -> tuple[np.ndarray | None, str | None]:
        """Gets a voice and text response from audio input and text context."""
        if not self._model or not self._processor:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        full_prompt = f"{self._persona_prompt}\n\nHere are some relevant memories from the past:\n{context}"
        conversation = [[
            {"role": "system", "content": [{"type": "text", "text": full_prompt}]},
            {"role": "user", "content": [{"type": "audio", "audio": audio_path}]}
        ]]
        return self._generate_response(conversation)

    def respond_to_text(self, text: str, context: str = "") -> tuple[np.ndarray | None, str | None]:
        """Gets a voice and text response from text input and context."""
        if not self._model or not self._processor:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        system_prompt = f"{self._persona_prompt}\n\nHere are some relevant memories from the past:\n{context}"
        conversation = [[
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "text", "text": text}]}
        ]]
        return self._generate_response(conversation)

    def _generate_response(self, conversation: list, do_sample: bool = True) -> tuple[np.ndarray | None, str | None]:
        inputs = self._processor(conversation, add_generation_prompt=True, tokenize=False)
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        output = self._model.generate(
            **inputs,
            max_new_tokens=self._max_new_tokens,
            do_sample=do_sample,
            temperature=0.7,
            top_p=0.9,
            use_cache=True,
            output_text=True
        )

        audio_values = self._model.codec_model.decode(output.permute(0, 2, 1)).audio_values
        text_response = self._processor.decode(output[0], skip_special_tokens=True)

        return audio_values[0].cpu().detach().numpy(), text_response

    def set_persona_prompt(self, prompt: str):
        self._persona_prompt = prompt
