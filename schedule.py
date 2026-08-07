"""Build the Healing Frames posting plan: 1 reel/day, Facebook + Instagram.

No teaser image or Stories for now -- reels only, until ~30-40 are published
and we've seen how the page grows organically. The wallpaper-pack CTA/funnel
is deferred until then too (see publish.py).

Facebook feed reel is scheduled server-side directly from here -- Meta fires
it even if every machine is off. Meta only allows scheduling 10min to 29 days
out, so for a 100-day calendar this must be re-run periodically (daily) to
"top up" newly-in-window days; already-scheduled days are skipped.

Instagram feed reel has no scheduling at all on Meta's side -- runner.py has
to fire it at the moment it's due. It goes into state/queue.json for
runner.py (run every 15-30 min, locally or via the GitHub Actions cron in
this repo) to publish when due.

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


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
        caption = publish.with_hashtags(narration[str(num)])
        video_path = f"videos/HealingFrames_{num:02d}_final.mp4"  # relative, repo-root

        in_window = min_time <= reel_time <= max_time
        tag = "" if in_window else "  (outside 29-day FB window, will schedule on a later run)"
        print(f"  day {i+1}: [reel] {reel_time.strftime('%Y-%m-%d %H:%M %Z')}  -- #{num}{tag}")

        if not args.live:
            continue

        state = fb_state.get(str(num), {})

        # Reels only for now -- no teaser image, no Stories, on either
        # platform. The wallpaper-pack CTA and its teaser-image funnel are
        # deferred until ~30-40 reels are published and we can see how the
        # page is growing organically first.
        #
        # Facebook feed reel -- schedule server-side, only once, only within
        # window. If Meta's spam throttle kicks in mid-run, stop attempting
        # FB calls for the rest of *this* run (retry on the next run) but
        # keep building the IG reel queue below regardless.
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

        # Instagram feed reel -- no scheduling, queue for runner.py
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
