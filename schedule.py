"""Build the Healing Frames posting plan: 1 reel/day + teaser image 1h before,
posted to Facebook feed, Instagram feed, Facebook Story, and Instagram Story.

Facebook feed (photo + reel) is scheduled server-side directly from here --
Meta fires it even if every machine is off. Meta only allows scheduling 10min
to 29 days out, so for a 100-day calendar this must be re-run periodically
(daily) to "top up" newly-in-window days; already-scheduled days are skipped.

Instagram feed (photo + reel) and both Stories have no scheduling at all on
Meta's side -- runner.py has to fire them at the moment they're due. Those
four go into state/queue.json for runner.py (run every 15-30 min, locally or
via the GitHub Actions cron in this repo) to publish when due.

Images are already committed to this repo's images/ folder, so their public
URL (for IG feed + IG Story) is static -- no per-day git push needed.

Dry run (default) prints the plan only. Pass --live to actually schedule on
Facebook and write/update queue.json + fb_state.json.
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import publish

RATE_LIMIT_DELAY = 8  # seconds between FB scheduling calls, to stay under Meta's spam throttle

HERE = os.path.dirname(os.path.abspath(__file__))
NARRATION_PATH = os.path.join(HERE, "narration.json")
QUEUE_PATH = os.path.join(HERE, "state", "queue.json")
FB_STATE_PATH = os.path.join(HERE, "state", "fb_state.json")
PLAN_PATH = os.path.join(HERE, "state", "plan.json")  # pins the day-1 anchor date across re-runs
IMAGES_RAW_BASE = "https://raw.githubusercontent.com/iqbal-jewel/healingframes-social/main/images"


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def find_image_ext(num):
    for ext in ("jpg", "jpeg", "png"):
        if os.path.exists(os.path.join(HERE, "images", f"Image_{num:02d}.{ext}")):
            return ext
    return None


def load_env():
    env = dict(os.environ)
    env_path = os.path.join(HERE, "..", "Automation", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env.setdefault(k, v)
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--time", default="19:00", help="Daily reel post time, HH:MM local")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--start-tomorrow", action="store_true", default=True)
    parser.add_argument("--live", action="store_true", help="Schedule on FB + write queue.json. Omit to dry-run.")
    args = parser.parse_args()

    env = load_env()
    page_id = env.get("HEALINGFRAMES_PAGE_ID")
    page_token = env.get("HEALINGFRAMES_PAGE_TOKEN")
    ig_user_id = env.get("HEALINGFRAMES_IG_USER_ID")
    if args.live and not (page_id and page_token and ig_user_id):
        raise SystemExit("Missing HEALINGFRAMES_PAGE_ID / HEALINGFRAMES_PAGE_TOKEN / HEALINGFRAMES_IG_USER_ID")

    tz = ZoneInfo(args.timezone)
    now = datetime.now(tz)
    post_time = dtime.fromisoformat(args.time)

    # The day-1 anchor is pinned on first run and reused on every subsequent
    # run (e.g. the daily top-up job) -- otherwise "start tomorrow" would
    # shift the whole 100-day mapping every time this script runs on a
    # different day.
    plan = _load_json(PLAN_PATH, None)
    if plan is None:
        start_date = (now + timedelta(days=1)).date() if args.start_tomorrow else now.date()
        plan = {"start_date": start_date.isoformat(), "time": args.time, "timezone": args.timezone}
        if args.live:
            _save_json(PLAN_PATH, plan)
    else:
        start_date = datetime.fromisoformat(plan["start_date"]).date()
        post_time = dtime.fromisoformat(plan["time"])
        tz = ZoneInfo(plan["timezone"])
        now = datetime.now(tz)

    with open(NARRATION_PATH, encoding="utf-8") as f:
        narration = json.load(f)
    nums = sorted(int(k) for k in narration.keys())

    queue = _load_json(QUEUE_PATH, [])
    fb_state = _load_json(FB_STATE_PATH, {})
    queued_keys = {(e["num"], e["type"]) for e in queue}

    min_time = now + timedelta(minutes=20)
    max_time = now + timedelta(days=29)
    fb_rate_limited = False  # once True, skip further FB attempts this run but keep building the queue

    print(f"{'LIVE' if args.live else 'DRY RUN'} -- planning {len(nums)} day(s), "
          f"starting {start_date} at {args.time} {args.timezone}:")

    for i, num in enumerate(nums):
        reel_time = datetime.combine(start_date + timedelta(days=i), post_time, tzinfo=tz)
        image_time = reel_time - timedelta(hours=1)
        caption = publish.with_hashtags(narration[str(num)])
        ext = find_image_ext(num)
        image_path = f"images/Image_{num:02d}.{ext}" if ext else None          # relative, repo-root
        image_url = f"{IMAGES_RAW_BASE}/Image_{num:02d}.{ext}" if ext else None
        video_path = f"videos/HealingFrames_{num:02d}_final.mp4"                # relative, repo-root

        in_window = min_time <= reel_time <= max_time
        tag = "" if in_window else "  (outside 29-day FB window, will schedule on a later run)"
        print(f"  day {i+1}: [img+story] {image_time.strftime('%Y-%m-%d %H:%M %Z')}  "
              f"[reel] {reel_time.strftime('%Y-%m-%d %H:%M %Z')}  -- #{num}{tag}")

        if not args.live:
            continue

        state = fb_state.get(str(num), {})

        # Facebook feed -- schedule server-side, only once, only within window.
        # If Meta's spam throttle kicks in mid-run, stop attempting FB calls for
        # the rest of *this* run (retry on the next run) but keep building the
        # IG/Story queue below for every remaining day regardless -- that part
        # never touches the FB API and isn't subject to this throttle.
        if not fb_rate_limited and in_window and state.get("fb_photo") != "scheduled" and image_path:
            try:
                fb_photo_id = publish.schedule_facebook_photo(
                    page_id, page_token, os.path.join(HERE, image_path), caption,
                    scheduled_publish_time=int(image_time.timestamp()),
                )
                state["fb_photo"] = "scheduled"
                state["fb_photo_id"] = fb_photo_id
                print(f"    FB photo scheduled -- {fb_photo_id}")
                time.sleep(RATE_LIMIT_DELAY)
            except RuntimeError as e:
                print(f"    FB photo scheduling FAILED, pausing FB attempts for this run: {e}")
                fb_rate_limited = True

        if not fb_rate_limited and in_window and state.get("fb_reel") != "scheduled":
            try:
                fb_reel_id = publish.schedule_facebook_reel(
                    page_id, page_token, os.path.join(HERE, video_path), caption,
                    scheduled_publish_time=int(reel_time.timestamp()),
                )
                state["fb_reel"] = "scheduled"
                state["fb_reel_id"] = fb_reel_id
                print(f"    FB reel scheduled -- {fb_reel_id}")
                time.sleep(RATE_LIMIT_DELAY)
            except RuntimeError as e:
                print(f"    FB reel scheduling FAILED, pausing FB attempts for this run: {e}")
                fb_rate_limited = True

        fb_state[str(num)] = state

        # Instagram feed + both Stories -- no scheduling, queue for runner.py
        if image_url:
            if (num, "ig_photo") not in queued_keys:
                queue.append({"day": i + 1, "num": num, "type": "ig_photo",
                               "due_time": image_time.isoformat(), "image_url": image_url,
                               "caption": caption, "published": False})
            if (num, "ig_story") not in queued_keys:
                queue.append({"day": i + 1, "num": num, "type": "ig_story",
                               "due_time": image_time.isoformat(), "image_url": image_url,
                               "published": False})
            if (num, "fb_story") not in queued_keys:
                queue.append({"day": i + 1, "num": num, "type": "fb_story",
                               "due_time": image_time.isoformat(), "image_path": image_path,
                               "published": False})
        if (num, "ig_reel") not in queued_keys:
            queue.append({"day": i + 1, "num": num, "type": "ig_reel",
                           "due_time": reel_time.isoformat(), "video_path": video_path,
                           "caption": caption, "published": False})

    if args.live:
        _save_json(QUEUE_PATH, queue)
        _save_json(FB_STATE_PATH, fb_state)
        print(f"\nQueue saved ({len(queue)} entries), FB state saved ({len(fb_state)} days).")


if __name__ == "__main__":
    main()
