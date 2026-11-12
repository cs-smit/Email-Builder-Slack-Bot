import os
import sys
import tempfile
from pathlib import Path

# Keep tests away from any real data/ folder and .env settings.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="emailbot-test-")
for var in ("LIBRARY_URL", "LIBRARY_DIR", "WORKER_URL", "RUNNER_SECRET", "FIGMA_TOKEN"):
    os.environ[var] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
