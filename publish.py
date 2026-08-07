"""Self-contained Meta Graph API publishing for the Healing Frames Facebook
Page + linked Instagram account. No imports from outside this repo, so
GitHub Actions can run it after checking out only this repo.

Daily posting set:
  - Facebook feed Reel/Photo  -- schedulable server-side (Meta fires it)
  - Instagram feed Reel/Photo -- immediate only, runner.py fires at due time
  - Facebook Story            -- immediate only, runner.py fires it
  - Instagram Story           -- immediate only, runner.py fires it

Facebook accepts raw bytes directly. Instagram never does -- feed photos and
Stories need a public image_url; Reels use Meta's resumable byte-upload
protocol (no public hosting needed for video).
"""
import os
import time

import requests

GRAPH = "https://graph.facebook.com/v21.0"

HASHTAGS = (
    "#healingframes #cozyvibes #slowliving #calmcontent "
    "#naturesoothes #quietmoments #eveningstillness #lofiaesthetic"
)
# Wallpaper-pack CTA is deferred until ~30-40 reels are published and we've
# seen how the page grows organically first -- add it back to with_hashtags()
# (and re-run the caption patch on already-scheduled FB posts) when ready.


def with_hashtags(text: str) -> str:
    return f"{text}\n\n{HASHTAGS}"


# --------------------------------------------------------------------------
# Facebook feed -- schedulable server-side
# --------------------------------------------------------------------------
def schedule_facebook_photo(page_id, page_token, image_path, caption, scheduled_publish_time):
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{GRAPH}/{page_id}/photos",
            params={
                "caption": caption, "published": "false",
                "scheduled_publish_time": scheduled_publish_time,
                "access_token": page_token,
            },
            files={"source": f},
        ).json()
    if "error" in resp:
        raise RuntimeError(f"FB photo schedule failed: {resp}")
    return resp["id"]


def schedule_facebook_reel(page_id, page_token, video_path, caption, scheduled_publish_time=None):
    start = requests.post(f"{GRAPH}/{page_id}/video_reels", params={
        "upload_phase": "start", "access_token": page_token,
    }).json()
    if "error" in start:
        raise RuntimeError(f"FB reel start failed: {start}")
    video_id, upload_url = start["video_id"], start["upload_url"]

    file_size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            upload_url,
            headers={"Authorization": f"OAuth {page_token}", "offset": "0", "file_size": str(file_size)},
            data=f.read(),
        )
    if not upload_resp.ok:
        raise RuntimeError(f"FB reel upload failed: {upload_resp.status_code} {upload_resp.text}")

    finish_params = {
        "upload_phase": "finish", "video_id": video_id,
        "description": caption, "access_token": page_token,
    }
    if scheduled_publish_time:
        finish_params["video_state"] = "SCHEDULED"
        finish_params["scheduled_publish_time"] = scheduled_publish_time
    else:
        finish_params["video_state"] = "PUBLISHED"

    finish = requests.post(f"{GRAPH}/{page_id}/video_reels", params=finish_params).json()
    if not finish.get("success"):
        raise RuntimeError(f"FB reel finish failed: {finish}")
    return video_id


# --------------------------------------------------------------------------
# Instagram feed -- immediate only
# --------------------------------------------------------------------------
def publish_instagram_photo(ig_user_id, page_token, image_url, caption):
    container = requests.post(f"{GRAPH}/{ig_user_id}/media", params={
        "image_url": image_url, "caption": caption, "access_token": page_token,
    }).json()
    if "error" in container:
        raise RuntimeError(f"IG photo container failed: {container}")
    publish = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", params={
        "creation_id": container["id"], "access_token": page_token,
    }).json()
    if "error" in publish:
        raise RuntimeError(f"IG photo publish failed: {publish}")
    return publish["id"]


def publish_instagram_reel(ig_user_id, page_token, video_path, caption):
    container = requests.post(f"{GRAPH}/{ig_user_id}/media", params={
        "media_type": "REELS", "upload_type": "resumable",
        "caption": caption, "access_token": page_token,
    }).json()
    if "error" in container:
        raise RuntimeError(f"IG reel container failed: {container}")
    container_id, upload_uri = container["id"], container["uri"]

    file_size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            upload_uri,
            headers={"Authorization": f"OAuth {page_token}", "offset": "0", "file_size": str(file_size)},
            data=f.read(),
        )
    if not upload_resp.ok:
        raise RuntimeError(f"IG reel upload failed: {upload_resp.status_code} {upload_resp.text}")

    for _ in range(30):
        status = requests.get(f"{GRAPH}/{container_id}", params={
            "fields": "status_code", "access_token": page_token,
        }).json()
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"IG reel processing failed: {status}")
        time.sleep(5)
    else:
        raise RuntimeError("IG reel processing timed out")

    publish = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", params={
        "creation_id": container_id, "access_token": page_token,
    }).json()
    if "error" in publish:
        raise RuntimeError(f"IG reel publish failed: {publish}")
    return publish["id"]


# --------------------------------------------------------------------------
# Stories -- immediate only on both platforms
# --------------------------------------------------------------------------
def publish_facebook_photo_story(page_id, page_token, image_path):
    with open(image_path, "rb") as f:
        upload = requests.post(
            f"{GRAPH}/{page_id}/photos",
            params={"published": "false", "access_token": page_token},
            files={"source": f},
        ).json()
    if "error" in upload:
        raise RuntimeError(f"FB story photo upload failed: {upload}")
    story = requests.post(f"{GRAPH}/{page_id}/photo_stories", params={
        "photo_id": upload["id"], "access_token": page_token,
    }).json()
    if "error" in story:
        raise RuntimeError(f"FB story publish failed: {story}")
    return story.get("post_id") or story.get("id")


def publish_instagram_photo_story(ig_user_id, page_token, image_url):
    container = requests.post(f"{GRAPH}/{ig_user_id}/media", params={
        "image_url": image_url, "media_type": "STORIES", "access_token": page_token,
    }).json()
    if "error" in container:
        raise RuntimeError(f"IG story container failed: {container}")
    publish = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", params={
        "creation_id": container["id"], "access_token": page_token,
    }).json()
    if "error" in publish:
        raise RuntimeError(f"IG story publish failed: {publish}")
    return publish["id"]
