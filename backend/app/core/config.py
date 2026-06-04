from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")


class Settings:
    def __init__(self):
        self.ARK_API_KEY = os.environ.get("ARK_API_KEY", "")
        self.ARK_BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        self.ARK_MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")
        self.ARK_LITE_MODEL = os.environ.get("ARK_LITE_MODEL", "")
        self.ARK_MINI_MODEL = os.environ.get("ARK_MINI_MODEL", "doubao-seed-2-0-mini-260215")
        self.ARK_MODEL_MODE = os.environ.get("ARK_MODEL_MODE", "pro").lower()
        self.ARK_AGENT_CONCURRENCY = self._int_env("ARK_AGENT_CONCURRENCY", 3, 1, 6)
        self.CANDIDATE_AGENT_TIMEOUT_SEC = self._int_env("CANDIDATE_AGENT_TIMEOUT_SEC", 180, 60, 300)
        self.AGENT_RUNTIME = os.environ.get("AGENT_RUNTIME", "auto").lower()
        self.SHOT_AGENT_CONCURRENCY = self._int_env("SHOT_AGENT_CONCURRENCY", 6, 1, 16)
        self.SHOT_AGENT_MAX_REVISIONS = self._int_env("SHOT_AGENT_MAX_REVISIONS", 2, 0, 3)
        self.SHOT_AGENT_TIMEOUT_SEC = self._int_env("SHOT_AGENT_TIMEOUT_SEC", 70, 20, 180)
        self.SHOT_AGENT_SOFT_TIMEOUT_SEC = self._int_env("SHOT_AGENT_SOFT_TIMEOUT_SEC", 28, 8, 120)
        self.CRITIC_AGENT_TIMEOUT_SEC = self._int_env("CRITIC_AGENT_TIMEOUT_SEC", 45, 15, 120)
        self.STORYBOARD_MATCH_CONCURRENCY = self._int_env("STORYBOARD_MATCH_CONCURRENCY", 6, 1, 12)
        self.SEEDREAM_MODEL = os.environ.get("SEEDREAM_MODEL", "doubao-seedream-5-0-260128")
        self.PROJECT_ROOT = PROJECT_ROOT
        self.ASSETS_INDEX = PROJECT_ROOT / "data" / "assets-index.json"
        self.OUTPUT_DIR = BACKEND_DIR / "outputs"
        self.UPLOAD_DIR = BACKEND_DIR / "uploads"
        self.PUBLIC_OUTPUT_DIR = PROJECT_ROOT / "output"

    def chat_model(self) -> str:
        if self.ARK_MODEL_MODE in {"lite", "fast", "test"} and self.ARK_LITE_MODEL:
            return self.ARK_LITE_MODEL
        return self.ARK_MODEL

    def mini_model(self) -> str:
        return self.ARK_MINI_MODEL or self.chat_model()

    def _int_env(self, name: str, default: int, min_value: int, max_value: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError:
            return default
        return max(min_value, min(max_value, value))


@lru_cache
def get_settings() -> Settings:
    return Settings()
