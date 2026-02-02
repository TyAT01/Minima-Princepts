from __future__ import annotations
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    app_name: str = "Aurelia Vale Companion"
    persona_yaml: Path = Field(default=Path(__file__).resolve().parent / "aurelia_sheet.yaml")
    persona_modules_dir: Path = Field(default=Path(__file__).resolve().parent / "persona_modules")
    data_dir: Path = Field(default=Path(__file__).resolve().parent / "data")

    # Chroma 1.0 config
    chroma_model_id: str = Field(default="FlashLabs/Chroma-4B")
    max_new_tokens: int = Field(default=100)

    # Discord
    discord_token: Optional[str] = None
    discord_guild_id: Optional[int] = None
    discord_voice_channel_id: Optional[int] = None

    # Twitch
    twitch_username: Optional[str] = None
    twitch_token: Optional[str] = None
    twitch_channel: Optional[str] = None

    # YouTube
    youtube_api_key: Optional[str] = None
    youtube_token: Optional[str] = None
    youtube_live_chat_id: Optional[str] = None

    # Output settings
    enable_local_audio: bool = True

    # Audio settings
    sample_rate: int = 24000
    discord_sample_rate: int = 48000

    # Web Dashboard
    web_port: int = 8000

    class Config:
        env_prefix = "AURELIA_VALE_"
        case_sensitive = False
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
