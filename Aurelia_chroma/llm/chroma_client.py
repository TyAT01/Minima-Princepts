from __future__ import annotations
import logging
import torch
from transformers import AutoModelForCausalLM, AutoProcessor, TextIteratorStreamer
import numpy as np
from threading import Thread
from typing import Generator, Tuple, Optional
from llm.filters import ContentFilter

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
        self._filter = ContentFilter()

    def load(self):
        """Loads the model and processor."""
        logger.info("Loading Chroma 1.0 model: %s", self._model_id)

        # Use bfloat16 if CUDA is available, otherwise float32
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

        try:
            # Optimization: Load in 4-bit if CUDA is available to save VRAM on RTX 3070
            quant_config = None
            if torch.cuda.is_available():
                from transformers import BitsAndBytesConfig
                quant_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True
                )

            self._model = AutoModelForCausalLM.from_pretrained(
                self._model_id,
                trust_remote_code=True,
                device_map="auto",
                torch_dtype=dtype,
                quantization_config=quant_config
            )
            self._processor = AutoProcessor.from_pretrained(self._model_id, trust_remote_code=True)
            logger.info("Chroma 1.0 model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load Chroma model: {e}")
            raise

    def respond_to_audio(self, audio_path: str, context: str = "") -> tuple[np.ndarray | None, str | None]:
        """Gets a voice and text response from audio input and text context."""
        logger.info("Generating response from audio input.")
        if not self._model or not self._processor:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        system_prompt = (
            f"{self._persona_prompt}\n\n"
            "CURRENT AWARENESS AND MEMORIES:\n"
            f"{context}\n\n"
            "You are currently in a live voice interaction. Respond naturally and stay in character."
        )
        conversation = [[
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "audio", "audio": audio_path}]}
        ]]
        audio, text = self._generate_response(conversation)
        return audio, self._filter.filter_text(text) if text else text

    def respond_to_text(self, text: str, context: str = "") -> tuple[np.ndarray | None, str | None]:
        """Gets a voice and text response from text input and context."""
        if not self._model or not self._processor:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        system_prompt = (
            f"{self._persona_prompt}\n\n"
            "CURRENT AWARENESS AND MEMORIES:\n"
            f"{context}\n\n"
            "Respond to the following input while staying in character."
        )
        conversation = [[
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "text", "text": text}]}
        ]]
        audio, text = self._generate_response(conversation)
        return audio, self._filter.filter_text(text) if text else text

    def stream_respond_to_text(self, text: str, context: str = "") -> Generator[str, None, None]:
        """Streams text response from text input and context."""
        if not self._model or not self._processor:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        system_prompt = (
            f"{self._persona_prompt}\n\n"
            "CURRENT AWARENESS AND MEMORIES:\n"
            f"{context}\n\n"
            "Respond to the following input while staying in character."
        )
        conversation = [[
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "text", "text": text}]}
        ]]

        inputs = self._processor(conversation, add_generation_prompt=True, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        streamer = TextIteratorStreamer(self._processor, skip_prompt=True, skip_special_tokens=True)

        generation_kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=self._max_new_tokens,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            use_cache=True
        )

        thread = Thread(target=self._model.generate, kwargs=generation_kwargs)
        thread.start()

        for new_text in streamer:
            yield new_text

    def stream_respond_to_audio(self, audio_path: str, context: str = "") -> Generator[str, None, None]:
        """Streams text response from audio input and context."""
        if not self._model or not self._processor:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        system_prompt = (
            f"{self._persona_prompt}\n\n"
            "CURRENT AWARENESS AND MEMORIES:\n"
            f"{context}\n\n"
            "You are currently in a live voice interaction. Respond naturally and stay in character."
        )
        conversation = [[
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "audio", "audio": audio_path}]}
        ]]

        inputs = self._processor(conversation, add_generation_prompt=True, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        streamer = TextIteratorStreamer(self._processor, skip_prompt=True, skip_special_tokens=True)

        generation_kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=self._max_new_tokens,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            use_cache=True
        )

        thread = Thread(target=self._model.generate, kwargs=generation_kwargs)
        thread.start()

        for new_text in streamer:
            yield new_text

    def generate_audio_for_fragment(self, fragment: str) -> Optional[np.ndarray]:
        """Generates audio for a specific text fragment (used for low-latency streaming pipeline)."""
        if not self._model or not self._processor:
            return None

        # To generate just audio for a text fragment, we feed it as assistant content
        # and hope the model generates the corresponding audio tokens.
        # This is a bit of a hack for multimodal models but often works if they are duplex.
        conversation = [[
            {"role": "assistant", "content": [{"type": "text", "text": fragment}]}
        ]]

        try:
            # We want the model to generate audio tokens for the text we just gave it.
            # Some models might need a specific prompt to 'read' the text.
            inputs = self._processor(conversation, add_generation_prompt=False, return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

            output = self._model.generate(
                **inputs,
                max_new_tokens=self._max_new_tokens, # Should be enough for the fragment
                do_sample=False,
                use_cache=True
            )

            if hasattr(self._model, "codec_model") and output.ndim == 3:
                audio_values = self._model.codec_model.decode(output.permute(0, 2, 1)).audio_values
                return audio_values[0].cpu().detach().numpy()
        except Exception as e:
            logger.error(f"Error generating audio for fragment: {e}")

        return None

    def _generate_response(self, conversation: list, do_sample: bool = True) -> tuple[np.ndarray | None, str | None]:
        try:
            # return_tensors="pt" is usually required for the model
            inputs = self._processor(conversation, add_generation_prompt=True, return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

            output = self._model.generate(
                **inputs,
                max_new_tokens=self._max_new_tokens,
                do_sample=do_sample,
                temperature=0.7,
                top_p=0.9,
                use_cache=True
            )

            # Robust extraction of audio and text
            audio_np = None
            text_response = None

            # Handle multimodal output extraction with extra safety
            try:
                if hasattr(self._model, "codec_model") and output.ndim == 3:
                    # Expected shape for multimodal output (batch, seq, codebooks)
                    audio_values = self._model.codec_model.decode(output.permute(0, 2, 1)).audio_values
                    audio_np = audio_values[0].cpu().detach().numpy()
                elif output.ndim == 2:
                    # Standard 2D output (batch, seq)
                    logger.debug("Output is 2D, attempting to decode as text only.")
            except (KeyError, ValueError, AttributeError, RuntimeError) as audio_err:
                logger.error(f"Failed to extract audio from output: {audio_err}")
            except Exception as e:
                logger.error(f"Unexpected error during audio extraction: {e}")

            # Text decoding - handle both prompt+output and interleaved formats
            # Usually we want only the newly generated tokens
            try:
                input_len = inputs.get("input_ids", torch.tensor([])).shape[-1]

                if output.ndim == 3:
                    # If 3D, take the first codebook (usually contains text tokens if interleaved)
                    generated_tokens = output[0, input_len:, 0]
                else:
                    generated_tokens = output[0, input_len:]

                text_response = self._processor.decode(generated_tokens, skip_special_tokens=True)
            except (KeyError, ValueError, AttributeError, RuntimeError) as text_err:
                logger.error(f"Failed to decode text from output: {text_err}")
                text_response = "I have the words, but they are tangled in my circuits."
            except Exception as e:
                logger.error(f"Unexpected error during text decoding: {e}")
                text_response = "I encountered a linguistic anomaly."

            return audio_np, text_response
        except Exception as e:
            logger.error(f"Error during generation: {e}")
            return None, "I encountered a cognitive glitch while trying to respond."

    def filter_text(self, text: str) -> str:
        """Applies the content filter to the given text."""
        return self._filter.filter_text(text)

    def set_persona_prompt(self, prompt: str):
        self._persona_prompt = prompt
