from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    app_name: str = "Aurelia Chroma Companion"
    data_dir: Path = Field(default=Path("data"))
    tmp_dir: Path = Field(default=Path("tmp"))
    persona_yaml: Path = Field(default=Path(__file__).resolve().parent / "aurelia_sheet.yaml")

    # LLM + Chroma configs
    chroma_model_name: str = Field(default="gpt2")
    chroma_voice_model: str = Field(default="facebook/s2t-small-librispeech-asr")
    chroma_tts_model: str = Field(default="facebook/fastspeech2-en-ljspeech")
    llama_cpp_url: str = Field(default="http://localhost:8080")

    # Discord
    discord_token: Optional[str] = None
    discord_guild_id: Optional[int] = None
    discord_voice_channel_id: Optional[int] = None

    # Audio
    sample_rate: int = 16000
    vad_aggressiveness: int = 2

    # Desktop UI
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8000

    class Config:
        env_prefix = "AURELIA_CHROMA_"
        case_sensitive = False


settings = Settings()
