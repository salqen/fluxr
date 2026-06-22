"""
InstaBot v3.1 — Railway Edition
================================
Spustenie lokálne: python bot_server.py
Railway: automaticky cez Procfile
"""

from flask import Flask, jsonify, request, send_from_directory, redirect, session
from flask_cors import CORS
import threading, time, random, os, json, sys, uuid, secrets
from datetime import datetime
from werkzeug.utils import secure_filename
import requests as req_lib

# ── SELENIUM — len lokálne, na Railway vypnuté ────────────────────────────────
SELENIUM_AVAILABLE = False
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    SELENIUM_AVAILABLE = True
except ImportError:
    pass

try:
    from ig_publisher import IGPublisher, PostScheduler
    PUBLISHER_AVAILABLE = True
except ImportError:
    PUBLISHER_AVAILABLE = False

# ── KONFIGURÁCIA cez ENV premenné ─────────────────────────────────────────────
META_APP_ID     = os.environ.get("META_APP_ID", "996438119804252")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
REDIRECT_URI    = os.environ.get("REDIRECT_URI", "http://localhost:5000/auth/callback")
SECRET_KEY      = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_URL        = os.environ.get("BASE_URL", "http://localhost:5000")  # Railway URL pre media
PORT            = int(os.environ.get("PORT", 5000))

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
CORS(app)
app.secret_key = SECRET_KEY
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# ── SÚBORY & PRIEČINKY ────────────────────────────────────────────────────────
# Na Railway /tmp pretrváva počas behu, resetuje sa pri redeploy
DATA_DIR      = os.environ.get("DATA_DIR", "/tmp/instabot_data")
MEDIA_DIR     = os.path.join(DATA_DIR, "media_uploads")
DB_FILE       = os.path.join(DATA_DIR, "seen_posts.txt")
CONFIG_FILE   = os.path.join(DATA_DIR, "bot_config.json")
STATS_FILE    = os.path.join(DATA_DIR, "bot_stats.json")
USERS_FILE    = os.path.join(DATA_DIR, "users.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "scheduled_posts.json")

os.makedirs(MEDIA_DIR, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}

# ── BOT STAV ──────────────────────────────────────────────────────────────────
bot_state = {
    "running": False, "blocked": False,
    "likes": 0, "comments": 0, "posts": 0,
    "likes_total": 0, "comments_total": 0, "elapsed": 0,
    "current_tag": "", "current_account": "", "log": [],
    "selenium_available": SELENIUM_AVAILABLE
}
bot_config    = {}
bot_thread    = None
stop_event    = threading.Event()
active_logins = {}
schedulers    = {}

# ── USERS ─────────────────────────────────────────────────────────────────────
def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def get_current_user() -> dict | None:
    uid = session.get("ig_user_id")
    if not uid:
        return None
    return load_users().get(uid)

def save_user(ig_user_id: str, username: str, token: str, avatar: str = ""):
    users = load_users()
    users[ig_user_id] = {
        "ig_user_id":   ig_user_id,
        "username":     username,
        "token":        token,
        "avatar":       avatar,
        "connected_at": datetime.now().isoformat()
    }
    save_users(users)
    if PUBLISHER_AVAILABLE and ig_user_id not in schedulers:
        pub = IGPublisher(token, ig_user_id)
        sch = PostScheduler(pub, SCHEDULE_FILE, MEDIA_DIR, add_log)
        sch.start()
        schedulers[ig_user_id] = sch

# ── HELPERS ───────────────────────────────────────────────────────────────────
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            bot_state["likes_total"]    = s.get("likes_total", 0)
            bot_state["comments_total"] = s.get("comments_total", 0)

def save_stats():
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump({"likes_total": bot_state["likes_total"],
                   "comments_total": bot_state["comments_total"]}, f)

def load_config():
    global bot_config
    default = {
        "mode": "smart_feed",
        "hashtags":       ["aimodelusa","aiinfluencer","virtualmodel","miamivibes","caligirls"],
        "comments_list":  ["Stunning! 😍","Love this vibe 🔥","Absolutely fire!","Insane work ✨","So good!"],
        "viral_accounts": ["lilmiquela","kuki_ai","fit_aitana","theshaderoom","wasted"],
        "pause_min": 9, "pause_max": 30,
        "posts_per_tag": 6, "posts_per_account": 5,
        "comment_prob": 0.15, "story_view_prob": 0.35,
        "feed_mode_enabled": True, "round_pause": 100,
        "like_enabled": True, "comment_enabled": True,
        "auto_stop": True, "max_daily_actions": 150, "human_scroll": True
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            default.update(json.load(f))
    bot_config = default
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(bot_config, f, ensure_ascii=False, indent=2)

def load_seen_posts() -> set:
    if not os.path.exists(DB_FILE):
        return set()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_post(url: str):
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def add_log(msg: str, level: str = "info"):
    entry = {"time": time.strftime("%H:%M:%S"), "msg": msg, "level": level}
    bot_state["log"].insert(0, entry)
    if len(bot_state["log"]) > 300:
        bot_state["log"].pop()
    print(f"[{entry['time']}] {msg}")

def human_delay(min_sec=8.0, max_sec=28.0):
    time.sleep(random.uniform(min_sec, max_sec))
    if random.random() < 0.18:
        time.sleep(random.uniform(20, 50))

# ── BOT LOGIKA (len ak Selenium dostupný) ────────────────────────────────────
def random_mouse_move(driver):
    try:
        actions = ActionChains(driver)
        for _ in range(random.randint(2, 5)):
            actions.move_by_offset(random.randint(-120, 120), random.randint(-80, 80))
        actions.perform()
        time.sleep(random.uniform(0.1, 0.4))
    except:
        pass

def find_comment_box(driver):
    selectors = [
        (By.XPATH, "//textarea[contains(@placeholder,'comment') or contains(@placeholder,'Comment')]"),
        (By.XPATH, "//textarea[contains(@placeholder,'koment')]"),
        (By.CSS_SELECTOR, "textarea[aria-label*='comment' i]"),
        (By.XPATH, "//form//textarea"),
        (By.CSS_SELECTOR, "textarea"),
    ]
    for by, sel in selectors:
        try:
            el = WebDriverWait(driver, 3).until(EC.presence_of_element_located((by, sel)))
            if el and el.is_displayed():
                return el
        except:
            continue
    return None

def click_comment_area(driver):
    try:
        btns = driver.find_elements(By.XPATH,
            "//*[contains(@aria-label,'Add a comment') or contains(@aria-label,'Pridajte komentár')]")
        if btns:
            driver.execute_script("arguments[0].click();", btns[0])
            time.sleep(0.8)
    except:
        pass

def view_stories(driver, account=None):
    if random.random() > bot_config.get("story_view_prob", 0.35):
        return
    try:
        if account:
            driver.get(f"https://www.instagram.com/{account}/")
            time.sleep(random.uniform(3, 5))
        rings = driver.find_elements(By.XPATH, "//canvas[contains(@class,'story')] | //span[@role='link']//img")
        if not rings:
            return
        driver.execute_script("arguments[0].click();", rings[0])
        time.sleep(random.uniform(8, 18))
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except:
            pass
        add_log("👁 Stories pozreté", "info")
    except:
        pass

def run_bot():
    if not SELENIUM_AVAILABLE:
        add_log("❌ Selenium nie je dostupný na tomto serveri. Bot funguje len lokálne.", "err")
        bot_state["running"] = False
        return

    bot_state["blocked"] = False
    actions_today = 0
    seen_posts    = load_seen_posts()
    session_start = time.time()

    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")

    try:
        driver = webdriver.Chrome(options=options)
        add_log("✅ Pripojené k Chrome", "ok")
    except Exception as e:
        add_log(f"❌ Chyba Chrome: {e}", "err")
        bot_state["running"] = False
        return

    while not stop_event.is_set():
        try:
            bot_state["elapsed"] = int(time.time() - session_start)

            if bot_config.get("feed_mode_enabled", True) and random.random() < 0.6:
                add_log("📱 Smart feed...", "info")
                driver.get(random.choice(["https://www.instagram.com/",
                                          "https://www.instagram.com/explore/"]))
                human_delay(10, 18)
                for _ in range(random.randint(4, 8)):
                    driver.execute_script(f"window.scrollBy(0, {random.randint(450, 900)});")
                    random_mouse_move(driver)
                    human_delay(4, 10)

            mode       = bot_config.get("mode", "hashtags")
            is_account = (mode == "viral_accounts")
            targets    = list(bot_config["viral_accounts"] if is_account else bot_config["hashtags"])
            max_per    = bot_config["posts_per_account"] if is_account else bot_config["posts_per_tag"]
            random.shuffle(targets)

            for target in targets[:6]:
                if stop_event.is_set():
                    break
                bot_state["current_account"] = target if is_account else ""
                bot_state["current_tag"]     = "" if is_account else target
                add_log(f"{'👤 @' if is_account else '🔎 #'}{target}", "info")

                url = (f"https://www.instagram.com/{target}/reels/" if is_account
                       else f"https://www.instagram.com/explore/tags/{target}/")
                driver.get(url)
                human_delay(6, 12)

                raw_links = driver.find_elements(By.XPATH,
                    "//a[contains(@href,'/p/') or contains(@href,'/reel/')]")
                post_links = []
                for a in raw_links:
                    href = a.get_attribute("href")
                    if href:
                        clean = href.split("?")[0].rstrip("/")
                        if clean not in seen_posts:
                            post_links.append((a, clean))

                if not post_links:
                    add_log(f"  ↳ Žiadne nové posty", "info")
                    continue

                random.shuffle(post_links)

                for post_el, post_url in post_links[:max_per]:
                    if stop_event.is_set() or actions_today >= bot_config.get("max_daily_actions", 150):
                        break
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", post_el)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", post_el)
                        human_delay(4, 9)
                    except:
                        continue

                    curr_url = driver.current_url.split("?")[0].rstrip("/")
                    if curr_url in seen_posts:
                        try:
                            driver.back(); time.sleep(2)
                        except:
                            pass
                        continue

                    bot_state["posts"] += 1
                    random_mouse_move(driver)

                    if bot_config.get("like_enabled", True) and random.random() < 0.80:
                        try:
                            like_btn = WebDriverWait(driver, 5).until(
                                EC.presence_of_element_located((By.XPATH,
                                    "//*[@aria-label='Like' or @aria-label='Páči sa mi to' or contains(@aria-label,'like')]")))
                            driver.execute_script("arguments[0].click();", like_btn)
                            bot_state["likes"] += 1
                            bot_state["likes_total"] += 1
                            save_stats()
                            add_log("  ❤️ Like", "ok")
                            actions_today += 1
                        except:
                            pass

                    if (bot_config.get("comment_enabled", True)
                            and random.random() < bot_config.get("comment_prob", 0.15)):
                        try:
                            driver.execute_script("window.scrollBy(0, 300);")
                            time.sleep(0.6)
                            click_comment_area(driver)
                            box = find_comment_box(driver)
                            if box:
                                driver.execute_script("arguments[0].focus();", box)
                                human_delay(0.5, 1.2)
                                msg = random.choice(bot_config["comments_list"])
                                for ch in msg:
                                    box.send_keys(ch)
                                    time.sleep(random.uniform(0.04, 0.14))
                                time.sleep(random.uniform(0.7, 1.3))
                                box.send_keys(Keys.RETURN)
                                bot_state["comments"] += 1
                                bot_state["comments_total"] += 1
                                save_stats()
                                add_log(f"  💬 {msg}", "ok")
                                actions_today += 1
                        except Exception as e:
                            add_log(f"  ⚠️ Komentár: {e}", "warn")

                    seen_posts.add(curr_url)
                    save_post(curr_url)
                    human_delay(bot_config.get("pause_min", 9), bot_config.get("pause_max", 30))

                    try:
                        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                        time.sleep(1.5)
                    except:
                        pass

                if random.random() < 0.4:
                    view_stories(driver, target if is_account else None)

            if actions_today >= bot_config.get("max_daily_actions", 150):
                add_log("🛡️ Denný limit — pauza 2h", "warn")
                time.sleep(7200)
                actions_today = 0

            if not stop_event.is_set():
                rp = int(bot_config.get("round_pause", 100))
                add_log(f"🏁 Kolo hotové. Čakám {rp}s...", "ok")
                human_delay(rp * 0.6, rp + 35)

        except Exception as e:
            add_log(f"⚠️ Chyba: {e}", "warn")
            human_delay(15, 35)

    add_log("⏹ Bot zastavený.", "warn")
    bot_state["running"] = False

# ── OAUTH ROUTES ──────────────────────────────────────────────────────────────
@app.route("/auth/login")
def auth_login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    oauth_url = (
        "https://www.facebook.com/v19.0/dialog/oauth"
        f"?client_id={META_APP_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"
        f"&state={state}"
        "&response_type=code"
    )
    return redirect(oauth_url)

@app.route("/auth/callback")
def auth_callback():
    code  = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return redirect("/?error=access_denied")

    if state != session.pop("oauth_state", None):
        return redirect("/?error=invalid_state")

    r1 = req_lib.get("https://graph.facebook.com/v19.0/oauth/access_token", params={
        "client_id":     META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri":  REDIRECT_URI,
        "code":          code
    }).json()

    if "error" in r1:
        return redirect(f"/?error={r1['error'].get('message','auth_error')}")

    short_token = r1["access_token"]

    r2 = req_lib.get("https://graph.facebook.com/v19.0/oauth/access_token", params={
        "grant_type":        "fb_exchange_token",
        "client_id":         META_APP_ID,
        "client_secret":     META_APP_SECRET,
        "fb_exchange_token": short_token
    }).json()

    long_token = r2.get("access_token", short_token)

    ig_user_id, ig_username, ig_avatar = None, None, ""
    try:
        pages = req_lib.get("https://graph.facebook.com/v19.0/me/accounts", params={
            "access_token": long_token
        }).json().get("data", [])

        for page in pages:
            ig_data = req_lib.get(f"https://graph.facebook.com/v19.0/{page['id']}", params={
                "fields":       "instagram_business_account",
                "access_token": page.get("access_token", long_token)
            }).json()
            ig_id = ig_data.get("instagram_business_account", {}).get("id")
            if ig_id:
                profile = req_lib.get(f"https://graph.facebook.com/v19.0/{ig_id}", params={
                    "fields":       "username,profile_picture_url,followers_count",
                    "access_token": long_token
                }).json()
                ig_user_id = ig_id
                ig_username = profile.get("username", "")
                ig_avatar   = profile.get("profile_picture_url", "")
                break
    except Exception as e:
        add_log(f"⚠️ IG account lookup: {e}", "warn")

    if not ig_user_id:
        me = req_lib.get("https://graph.facebook.com/v19.0/me", params={
            "fields": "id,name", "access_token": long_token
        }).json()
        ig_user_id  = me.get("id", str(uuid.uuid4())[:8])
        ig_username = me.get("name", "user")

    save_user(ig_user_id, ig_username, long_token, ig_avatar)
    session["ig_user_id"] = ig_user_id
    session["ig_username"] = ig_username
    session["ig_avatar"]   = ig_avatar

    add_log(f"✅ Prihlásený: @{ig_username}", "ok")
    return redirect("/dashboard")

@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect("/")

# ── STRÁNKY ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if session.get("ig_user_id"):
        return redirect("/dashboard")
    return send_from_directory(".", "login.html")

@app.route("/dashboard")
def dashboard():
    if not session.get("ig_user_id"):
        return redirect("/")
    return send_from_directory(".", "dashboard.html")

@app.route("/media/<path:filename>")
def serve_media(filename):
    return send_from_directory(MEDIA_DIR, filename)

# ── API — SESSION ─────────────────────────────────────────────────────────────
@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in":  True,
        "username":   user.get("username"),
        "avatar":     user.get("avatar", ""),
        "ig_user_id": user.get("ig_user_id"),
        "has_token":  bool(user.get("token"))
    })

# ── API — BOT ─────────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    return jsonify(bot_state)

@app.route("/api/start", methods=["POST"])
def api_start():
    global bot_thread
    if not SELENIUM_AVAILABLE:
        return jsonify({"ok": False, "msg": "Selenium bot nie je dostupný na Railway. Funguje len lokálne."})
    if bot_state["running"]:
        return jsonify({"ok": False, "msg": "Bot už beží"})
    data = request.get_json(silent=True)
    if data and "config" in data:
        bot_config.update(data["config"])
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_config, f, ensure_ascii=False, indent=2)
    bot_state.update({"running": True, "likes": 0, "comments": 0, "posts": 0, "elapsed": 0, "log": []})
    stop_event.clear()
    add_log("▶ Bot spustený", "ok")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not bot_state["running"]:
        return jsonify({"ok": False, "msg": "Bot nebeží"})
    stop_event.set()
    add_log("⏹ Zastavujem...", "warn")
    return jsonify({"ok": True})

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(bot_config)

@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json()
    if data:
        bot_config.update(data)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_config, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})

@app.route("/api/clear_seen", methods=["POST"])
def api_clear_seen():
    open(DB_FILE, "w").close()
    add_log("🗑 História vymazaná", "warn")
    return jsonify({"ok": True})

# ── API — PUBLISHER ───────────────────────────────────────────────────────────
@app.route("/api/pub/upload", methods=["POST"])
def pub_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Žiadny súbor"})
    f   = request.files["file"]
    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"ok": False, "error": f"Nepodporovaný formát: {ext}"})
    uid      = str(uuid.uuid4())[:8]
    new_name = f"{uid}{ext}"
    f.save(os.path.join(MEDIA_DIR, new_name))
    # Použije Railway URL namiesto localhost
    url = f"{BASE_URL}/media/{new_name}"
    return jsonify({"ok": True, "filename": new_name, "url": url})

@app.route("/api/pub/publish", methods=["POST"])
def pub_publish_now():
    user = get_current_user()
    if not user or not user.get("token"):
        return jsonify({"ok": False, "error": "Nie si prihlásený alebo chýba token"})
    if not PUBLISHER_AVAILABLE:
        return jsonify({"ok": False, "error": "ig_publisher.py chýba"})

    data     = request.get_json()
    ptype    = data.get("type", "post")
    url      = data.get("media_url", "")
    caption  = data.get("caption", "")
    hashtags = data.get("hashtags", [])
    mentions = data.get("mentions", [])

    if not url:
        return jsonify({"ok": False, "error": "Chýba URL média"})

    pub = IGPublisher(user["token"], user["ig_user_id"])
    add_log(f"📤 Publikujem [{ptype.upper()}] pre @{user['username']}...", "info")

    if ptype == "post":
        result = pub.publish_photo(url, caption, hashtags, mentions)
    elif ptype == "reel":
        result = pub.publish_reel(url, caption, hashtags, mentions)
    elif ptype == "story":
        if url.lower().split("?")[0].endswith((".mp4", ".mov")):
            result = pub.publish_story_video(url)
        else:
            result = pub.publish_story_photo(url)
    else:
        return jsonify({"ok": False, "error": "Neznámy typ"})

    if result.get("ok"):
        add_log(f"✅ Zverejnené! IG ID: {result.get('post_id')}", "ok")
    else:
        add_log(f"❌ Chyba: {result.get('error')}", "err")
    return jsonify(result)

@app.route("/api/schedule", methods=["GET"])
def sched_list():
    user = get_current_user()
    uid  = user["ig_user_id"] if user else None
    sch  = schedulers.get(uid) if uid else None
    return jsonify(sch.get_all() if sch else [])

@app.route("/api/schedule", methods=["POST"])
def sched_add():
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "Nie si prihlásený"})
    uid = user["ig_user_id"]
    if uid not in schedulers and PUBLISHER_AVAILABLE:
        pub = IGPublisher(user["token"], uid)
        sch = PostScheduler(pub, SCHEDULE_FILE, MEDIA_DIR, add_log)
        sch.start()
        schedulers[uid] = sch
    sch = schedulers.get(uid)
    if not sch:
        return jsonify({"ok": False, "error": "Plánovač nedostupný"})
    data    = request.get_json()
    post_id = sch.add(
        post_type    = data.get("type", "post"),
        media_url    = data.get("media_url", ""),
        caption      = data.get("caption", ""),
        hashtags     = data.get("hashtags", []),
        mentions     = data.get("mentions", []),
        music        = data.get("music", ""),
        scheduled_at = data.get("scheduled_at")
    )
    return jsonify({"ok": True, "id": post_id})

@app.route("/api/schedule/<post_id>", methods=["DELETE"])
def sched_delete(post_id):
    user = get_current_user()
    uid  = user["ig_user_id"] if user else None
    sch  = schedulers.get(uid) if uid else None
    if not sch:
        return jsonify({"ok": False})
    return jsonify({"ok": sch.delete(post_id)})

@app.route("/api/caption", methods=["POST"])
def gen_caption():
    data     = request.get_json()
    ptype    = data.get("type", "post")
    niche    = data.get("niche", "lifestyle")
    keywords = data.get("keywords", "")
    lang     = data.get("lang", "en")
    tone     = data.get("tone", "engaging")
    prompt = (
        f"You are a professional Instagram content creator. "
        f"Generate 3 different {tone} captions for an Instagram {ptype} "
        f"in the '{niche}' niche. Language: {lang}. "
        f"Keywords: {keywords}. "
        f"Each caption: strong hook, relevant emojis, call-to-action, 10-15 trending hashtags. "
        f'Respond ONLY in JSON: {{"captions": [{{"text": "...", "hashtags": ["tag1",...]}}]}}'
    )
    try:
        r = req_lib.post("https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={"model": "claude-sonnet-4-6", "max_tokens": 1000,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=30)
        text = r.json().get("content", [{}])[0].get("text", "{}")
        import re
        m = re.search(r'\{.*\}', text, re.DOTALL)
        parsed = json.loads(m.group()) if m else {}
        return jsonify({"ok": True, "result": parsed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── HEALTH CHECK pre Railway ──────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "publisher": PUBLISHER_AVAILABLE, "selenium": SELENIUM_AVAILABLE})

# ── START ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_stats()
    load_config()
    if PUBLISHER_AVAILABLE:
        for uid, u in load_users().items():
            if u.get("token") and u.get("ig_user_id"):
                pub = IGPublisher(u["token"], u["ig_user_id"])
                sch = PostScheduler(pub, SCHEDULE_FILE, MEDIA_DIR, add_log)
                sch.start()
                schedulers[uid] = sch

    print("\n" + "="*60)
    print("  InstaBot v3.1 — Railway Edition")
    print(f"  Publisher:  {'✅' if PUBLISHER_AVAILABLE else '⚠️ ig_publisher.py chýba'}")
    print(f"  Selenium:   {'✅ dostupný' if SELENIUM_AVAILABLE else '❌ nedostupný (Railway mode)'}")
    print(f"  Port:       {PORT}")
    print(f"  Base URL:   {BASE_URL}")
    print("="*60 + "\n")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
