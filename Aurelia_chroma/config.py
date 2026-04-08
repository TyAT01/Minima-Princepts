from __future__ import annotations
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    app_name: str = "Aurelia Chroma Companion"
    persona_yaml: Path = Field(default=Path(__file__).resolve().parent / "aurelia_sheet.yaml")

    # Chroma 1.0 config
    chroma_model_id: str = Field(default="FlashLabs/Chroma-4B")
    max_new_tokens: int = Field(default=100)

    # Discord
    discord_token: Optional[str] = None
    discord_guild_id: Optional[int] = None
    discord_voice_channel_id: Optional[int] = None

    # Audio settings
    sample_rate: int = 24000
    discord_sample_rate: int = 48000

    class Config:
        env_prefix = "AURELIA_CHROMA_"
        case_sensitive = False
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
