from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
_data_path = Path(os.getenv("FORECASTLAB_DATA_DIR", "data"))
DATA_DIR = (_data_path if _data_path.is_absolute() else ROOT / _data_path).resolve()
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
MODEL_PROVIDER = "qwen" if QWEN_API_KEY else "deepseek"
MODEL_API_KEY = QWEN_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")
MODEL_BASE_URL = (os.getenv("QWEN_BASE_URL", "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1")
                  if QWEN_API_KEY else os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
MODEL_NAME = (os.getenv("QWEN_MODEL", "qwen3.8-flash")
              if QWEN_API_KEY else os.getenv("DEEPSEEK_MODEL", "deepseek-flash"))
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
SEARCH_PROXY = os.getenv("FORECASTLAB_SEARCH_PROXY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
MAX_CALLS = int(os.getenv("FORECASTLAB_MAX_CALLS", "18"))
MAX_SECONDS = int(os.getenv("FORECASTLAB_MAX_SECONDS", "300"))
LIVE_CUTOFF_GRACE_SECONDS = int(os.getenv("FORECASTLAB_LIVE_CUTOFF_GRACE_SECONDS", "900"))
MODEL_TEMPERATURE = float(os.getenv("FORECASTLAB_MODEL_TEMPERATURE", "0"))
if not 0 <= MODEL_TEMPERATURE <= 2:
    raise ValueError("FORECASTLAB_MODEL_TEMPERATURE 必须在 0–2 之间")

# Opt-in research shadow forecast: extra model call; never replaces the main result.
SHADOW_FULL = os.getenv("FORECASTLAB_SHADOW", "0").strip().lower() not in {"", "0", "false", "no"}

_frontend_path = Path(os.getenv("FORECASTLAB_FRONTEND_DIR", "frontend/dist"))
FRONTEND_DIR = (_frontend_path if _frontend_path.is_absolute() else ROOT / _frontend_path).resolve()
