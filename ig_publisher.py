"""
ig_publisher.py — Instagram Graph API Publisher
================================================
Auto-publikovanie postov, reelsov a stories cez oficiálne IG Graph API.
Vyžaduje: Instagram Business/Creator účet prepojený s Facebook Page.

Nastavenie (jednorázové):
  1. developers.facebook.com → Nová app → Instagram Graph API
  2. Pridaj produkt: Instagram Graph API
  3. Nastavenia → IG Basic Display → prepoj Business účet
  4. Vygeneruj Access Token (odporúčame dlhodobý 60-dňový)
  5. Vlož token + ig_user_id do dashboard → Nastavenia

Dokumentácia: https://developers.facebook.com/docs/instagram-api/guides/content-publishing
"""

import requests
import time
import json
import os
import uuid
import threading
from datetime import datetime


GRAPH_VERSION = "v19.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"


class IGPublisher:
    def __init__(self, access_token: str, ig_user_id: str, base_server_url: str = ""):
        self.token = access_token
        self.user_id = ig_user_id
        self.base_server_url = base_server_url.rstrip("/")  # pre lokálne súbory

    def _post(self, endpoint: str, params: dict) -> dict:
        params["access_token"] = self.token
        try:
            r = requests.post(f"{BASE_URL}/{endpoint}", params=params, timeout=60)
            return r.json()
        except Exception as e:
            return {"error": {"message": str(e)}}

    def _get(self, endpoint: str, params: dict = {}) -> dict:
        params["access_token"] = self.token
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=30)
            return r.json()
        except Exception as e:
            return {"error": {"message": str(e)}}

    def _wait_for_video(self, container_id: str, max_wait: int = 300) -> bool:
        """Čakaj kým IG spracuje video (max 5 minút)."""
        for _ in range(max_wait // 10):
            data = self._get(container_id, {"fields": "status_code,status"})
            status = data.get("status_code", "")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                return False
            time.sleep(10)
        return False

    def _publish_container(self, container_id: str) -> dict:
        data = self._post(f"{self.user_id}/media_publish", {"creation_id": container_id})
        return {
            "ok": "id" in data,
            "post_id": data.get("id"),
            "error": data.get("error", {}).get("message") if "error" in data else None
        }

    # ── FOTO POST ────────────────────────────────────────────────────────────
    def publish_photo(self, image_url: str, caption: str = "", hashtags: list = [], mentions: list = []) -> dict:
        full_caption = caption
        if mentions:
            full_caption += "\n" + " ".join(f"@{m.lstrip('@')}" for m in mentions)
        if hashtags:
            full_caption += "\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

        data = self._post(f"{self.user_id}/media", {
            "image_url": image_url,
            "caption": full_caption
        })
        if "id" not in data:
            return {"ok": False, "error": data.get("error", {}).get("message", "Neznáma chyba")}

        time.sleep(3)
        return self._publish_container(data["id"])

    # ── REEL ─────────────────────────────────────────────────────────────────
    def publish_reel(self, video_url: str, caption: str = "", hashtags: list = [],
                     mentions: list = [], share_to_feed: bool = True) -> dict:
        full_caption = caption
        if mentions:
            full_caption += "\n" + " ".join(f"@{m.lstrip('@')}" for m in mentions)
        if hashtags:
            full_caption += "\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

        data = self._post(f"{self.user_id}/media", {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": full_caption,
            "share_to_feed": str(share_to_feed).lower()
        })
        if "id" not in data:
            return {"ok": False, "error": data.get("error", {}).get("message", "Neznáma chyba")}

        container_id = data["id"]
        if not self._wait_for_video(container_id):
            return {"ok": False, "error": "Spracovanie videa zlyhalo alebo vypršal čas"}

        return self._publish_container(container_id)

    # ── STORY ────────────────────────────────────────────────────────────────
    def publish_story_photo(self, image_url: str) -> dict:
        data = self._post(f"{self.user_id}/media", {
            "image_url": image_url,
            "media_type": "IMAGE"
        })
        if "id" not in data:
            return {"ok": False, "error": data.get("error", {}).get("message", "Neznáma chyba")}
        time.sleep(2)
        return self._publish_container(data["id"])

    def publish_story_video(self, video_url: str) -> dict:
        data = self._post(f"{self.user_id}/media", {
            "video_url": video_url,
            "media_type": "VIDEO"
        })
        if "id" not in data:
            return {"ok": False, "error": data.get("error", {}).get("message", "Neznáma chyba")}

        container_id = data["id"]
        if not self._wait_for_video(container_id, max_wait=120):
            return {"ok": False, "error": "Spracovanie story videa zlyhalo"}

        return self._publish_container(container_id)

    # ── TOKEN CHECK ──────────────────────────────────────────────────────────
    def verify_token(self) -> dict:
        data = self._get(self.user_id, {"fields": "id,name,username,followers_count"})
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message")}
        return {"ok": True, "username": data.get("username"), "followers": data.get("followers_count")}


# ── SCHEDULER ────────────────────────────────────────────────────────────────
class PostScheduler:
    def __init__(self, publisher: IGPublisher, schedule_file: str = "scheduled_posts.json",
                 media_dir: str = "media_uploads", log_fn=None):
        self.publisher = publisher
        self.schedule_file = schedule_file
        self.media_dir = media_dir
        self.log = log_fn or (lambda msg, lvl="info": print(f"[SCHED] {msg}"))
        os.makedirs(media_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

    def _load(self) -> list:
        if not os.path.exists(self.schedule_file):
            return []
        with open(self.schedule_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, posts: list):
        with open(self.schedule_file, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)

    def add(self, post_type: str, media_url: str, caption: str = "", hashtags: list = [],
            mentions: list = [], music: str = "", scheduled_at: str = None) -> str:
        post_id = str(uuid.uuid4())[:8]
        entry = {
            "id": post_id,
            "type": post_type,           # post | reel | story
            "media_url": media_url,
            "caption": caption,
            "hashtags": hashtags,
            "mentions": mentions,
            "music": music,              # informatívne - IG API nepodporuje priamo
            "scheduled_at": scheduled_at or datetime.now().isoformat(),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "result": None
        }
        with self._lock:
            posts = self._load()
            posts.append(entry)
            self._save(posts)
        return post_id

    def delete(self, post_id: str) -> bool:
        with self._lock:
            posts = self._load()
            before = len(posts)
            posts = [p for p in posts if p["id"] != post_id]
            self._save(posts)
            return len(posts) < before

    def get_all(self) -> list:
        return self._load()

    def _publish_entry(self, entry: dict) -> dict:
        t = entry["type"]
        url = entry["media_url"]
        caption = entry.get("caption", "")
        hashtags = entry.get("hashtags", [])
        mentions = entry.get("mentions", [])

        if t == "post":
            return self.publisher.publish_photo(url, caption, hashtags, mentions)
        elif t == "reel":
            return self.publisher.publish_reel(url, caption, hashtags, mentions)
        elif t == "story":
            ext = url.split("?")[0].lower()
            if ext.endswith((".mp4", ".mov")):
                return self.publisher.publish_story_video(url)
            else:
                return self.publisher.publish_story_photo(url)
        return {"ok": False, "error": "Neznámy typ"}

    def _run_loop(self):
        self.log("📅 Plánovač spustený", "ok")
        while not self._stop.is_set():
            now = datetime.now()
            with self._lock:
                posts = self._load()
                changed = False
                for entry in posts:
                    if entry["status"] != "pending":
                        continue
                    try:
                        sched = datetime.fromisoformat(entry["scheduled_at"])
                    except:
                        continue
                    if now >= sched:
                        self.log(f"📤 Publikujem [{entry['type'].upper()}] {entry['id']}", "info")
                        result = self._publish_entry(entry)
                        entry["status"] = "published" if result.get("ok") else "failed"
                        entry["result"] = result
                        changed = True
                        if result.get("ok"):
                            self.log(f"✅ Zverejnené! ID: {result.get('post_id')}", "ok")
                        else:
                            self.log(f"❌ Chyba: {result.get('error')}", "err")
                if changed:
                    self._save(posts)
            self._stop.wait(60)  # check každú minútu
        self.log("⏹ Plánovač zastavený", "warn")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
