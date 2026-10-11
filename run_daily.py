"""Run by GitHub Actions every morning."""
from dotenv import load_dotenv

load_dotenv()

from lowtide import pipeline  # noqa: E402

if __name__ == "__main__":
    ran = pipeline.run("scheduled")
    print("Run finished." if ran else "A run was already in progress; skipped.")
