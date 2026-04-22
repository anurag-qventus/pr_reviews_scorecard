from dotenv import load_dotenv
import os

load_dotenv()

AUTHORS = ["<userid1>", "<userid2>", "<userid3>", "<userid4>", "<userid5>"]
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Azure OpenAI Configurations
AZURE_OPENAI_API_BASE = os.getenv("AZURE_OPENAI_API_BASE")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_MODEL_NAME = "gpt-4o"

# ── Langfuse ───────────────────────────────────────
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_HOST       = os.getenv("LANGFUSE_HOST", "https://langfuse-dev.qventus-int.com")

DATA_DIR = f'data/'
FILE_NAME = f'data/'

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}