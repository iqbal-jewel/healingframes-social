# healingframes-social

Publishing pipeline for the Healing Frames Facebook Page + linked Instagram
account (`healing_frames_us`).

- `videos/` -- 100 finished reels (VO narration + cursive captions baked in)
- `images/` -- 100 teaser images (same scene as the matching reel)
- `narration.json` -- the poetic VO/caption line per clip number
- `publish.py` -- self-contained Meta Graph API calls (no external imports,
  so GitHub Actions can run this after checking out only this repo)
- `schedule.py` -- builds the 100-day posting plan: 1 reel/day + a teaser
  image (feed + Story on both platforms) 1h before. Facebook feed posts are
  scheduled server-side directly; run with `--live` to actually schedule +
  write `state/queue.json`. Re-run periodically (daily) to "top up" Facebook
  scheduling as new days enter Meta's 29-day scheduling window.
- `runner.py` -- fires whatever's due in `state/queue.json` (Instagram feed
  + both Stories have no server-side scheduling on Meta's side). Run every
  15-30 min, locally or via `.github/workflows/runner.yml`.

## Local setup

Requires `HEALINGFRAMES_PAGE_ID`, `HEALINGFRAMES_PAGE_TOKEN`,
`HEALINGFRAMES_IG_USER_ID` as env vars (or in `../Automation/.env`).
