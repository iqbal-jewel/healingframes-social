"""Publish due Instagram feed + Story posts from state/queue.json.

Facebook feed posts are scheduled server-side by schedule.py and need no
runner. Everything else (IG feed photo/reel, FB Story, IG Story) has no
scheduling on Meta's side and must be fired here, at the moment it's due.
Run every 15-30 min -- locally (Windows Task Scheduler) or via the GitHub
Actions cron in .github/workflows/runner.yml.
"""
import json
import os
from datetime import datetime, timezone

import publish

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(HERE, "state", "queue.json")
LOG_PATH = os.path.join(HERE, "state", "runner.log")


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    if not os.path.exists(QUEUE_PATH):
        log("No queue file, nothing to do.")
        return

    with open(QUEUE_PATH, encoding="utf-8") as f:
        queue = json.load(f)

    ig_user_id = os.environ.get("HEALINGFRAMES_IG_USER_ID")
    page_id = os.environ.get("HEALINGFRAMES_PAGE_ID")
    page_token = os.environ.get("HEALINGFRAMES_PAGE_TOKEN")
    if not (ig_user_id and page_token):
        raise SystemExit("Missing HEALINGFRAMES_IG_USER_ID / HEALINGFRAMES_PAGE_TOKEN")

    now = datetime.now(timezone.utc)
    changed = False

    # image/story posts before the reel, if several are due in the same run
    order = {"ig_photo": 0, "fb_story": 1, "ig_story": 2, "ig_reel": 3}
    queue.sort(key=lambda e: (e["due_time"], order.get(e["type"], 9)))

    for entry in queue:
        if entry.get("published"):
            continue
        due = datetime.fromisoformat(entry["due_time"])
        if due > now:
            continue

        etype = entry["type"]
        try:
            if etype == "ig_photo":
                media_id = publish.publish_instagram_photo(
                    ig_user_id, page_token, entry["image_url"], entry["caption"],
                )
            elif etype == "ig_story":
                media_id = publish.publish_instagram_photo_story(
                    ig_user_id, page_token, entry["image_url"],
                )
            elif etype == "fb_story":
                if not page_id:
                    raise RuntimeError("Missing HEALINGFRAMES_PAGE_ID for fb_story")
                if not os.path.exists(entry["image_path"]):
                    raise RuntimeError(f"image not checked out: {entry['image_path']}")
                media_id = publish.publish_facebook_photo_story(
                    page_id, page_token, entry["image_path"],
                )
            elif etype == "ig_reel":
                if not os.path.exists(entry["video_path"]):
                    raise RuntimeError(f"video not checked out: {entry['video_path']}")
                media_id = publish.publish_instagram_reel(
                    ig_user_id, page_token, entry["video_path"], entry["caption"],
                )
            else:
                log(f"unknown entry type {etype!r}, skipping")
                continue

            entry["published"] = True
            entry["media_id"] = media_id
            entry["published_at"] = now.isoformat()
            log(f"Published day {entry['day']} #{entry['num']} ({etype}) -- media_id {media_id}")
            changed = True
        except Exception as e:
            log(f"FAILED day {entry['day']} #{entry['num']} ({etype}): {e}")

    if changed:
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)

    pending = sum(1 for e in queue if not e.get("published"))
    log(f"Done. {pending} post(s) still pending.")


if __name__ == "__main__":
    main()
