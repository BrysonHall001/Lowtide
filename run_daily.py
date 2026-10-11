"""Run by GitHub Actions every morning: one search per person with a profile."""
from dotenv import load_dotenv

load_dotenv()

from lowtide import db, pipeline  # noqa: E402

if __name__ == "__main__":
    db.init()
    for uid in db.all_user_ids():
        if not db.get_criteria(uid):
            continue
        try:
            ran = pipeline.run(uid, "scheduled")
            print(f"User {uid}: {'done' if ran else 'skipped (already running)'}")
        except Exception as e:
            print(f"User {uid}: failed: {e}")
