#!/usr/bin/env python3
"""
fluxr — lokálny worker (Reach Booster)
======================================
Beží na TVOJOM počítači. Appka na Railway sa stará o publikovanie a plánovač;
tento worker robí lajky/komenty cez tvoj lokálny Chrome (port 9222).

Ovládaš ho z webu: na Railway dashboarde stlačíš Štart/Stop, worker to zachytí
a podľa toho spustí/zastaví bota. Stav a logy posiela späť na web.

Spustenie (najjednoduchšie cez run_local_worker.bat), alebo ručne:
    set FLUXR_SERVER=https://fluxr.bropri.sk
    set AGENT_TOKEN=<rovnaký token ako v Railway Variables>
    python bot_worker.py

Podmienky:
    1) Chrome bežiaci s:  --remote-debugging-port=9222
    2) v tom Chrome prihlásený Instagram
    3) selenium nainštalovaný:  pip install selenium
"""
import os
import sys
import time
import threading

import requests

# ── Konfigurácia ──────────────────────────────────────────────────────────────
SERVER = os.environ.get("FLUXR_SERVER", "https://fluxr.bropri.sk").rstrip("/")
TOKEN  = os.environ.get("AGENT_TOKEN", "")
POLL   = float(os.environ.get("FLUXR_POLL", "4"))          # ako často sa pýtať servera (s)

if not TOKEN:
    print("❌ CHYBA: nastav premennú AGENT_TOKEN (rovnakú ako v Railway → Variables).")
    sys.exit(1)

# Reuse celej bot logiky zo servera (rovnaký Selenium kód, žiadna duplicita)
try:
    import bot_server as bs
except Exception as e:
    print(f"❌ Nepodarilo sa načítať bot_server.py: {e}")
    print("   Spúšťaj worker z priečinka, kde je bot_server.py a ig_publisher.py.")
    sys.exit(1)

if not bs.SELENIUM_AVAILABLE:
    print("❌ Selenium nie je nainštalovaný v tomto Pythone. Spusti:  pip install selenium")
    sys.exit(1)

bs.load_config()   # default + uložený config
bs.load_stats()    # načíta doterajšie súčty lajkov/komentov

_bot_thread = None
_last_start = 0.0


def _bot_running() -> bool:
    return _bot_thread is not None and _bot_thread.is_alive()


def start_bot():
    global _bot_thread, _last_start
    if _bot_running():
        return
    _last_start = time.time()
    bs.stop_event.clear()
    bs.bot_state.update({"running": True, "likes": 0, "comments": 0, "posts": 0, "elapsed": 0})
    bs.add_log("▶ Štart z webu — pripájam sa na Chrome (9222)…", "ok")
    _bot_thread = threading.Thread(target=bs.run_bot, daemon=True)
    _bot_thread.start()


def stop_bot():
    if _bot_running():
        bs.stop_event.set()


def _snapshot() -> dict:
    keys = ("running", "blocked", "likes", "comments", "posts",
            "likes_total", "comments_total", "elapsed",
            "current_tag", "current_account", "log")
    return {k: bs.bot_state.get(k) for k in keys}


def report():
    try:
        requests.post(f"{SERVER}/api/agent/report",
                      json={"token": TOKEN, "state": _snapshot()}, timeout=8)
    except Exception:
        pass


# ── Hlavná slučka ─────────────────────────────────────────────────────────────
print("=" * 60)
print("  fluxr — lokálny worker")
print(f"  Server:  {SERVER}")
print("  Stav:    pripájam sa… (na webe choď na Bot → Štart)")
print("  Toto okno nechaj otvorené, kým chceš mať bota dostupného.")
print("=" * 60)

_warned_offline = False

while True:
    try:
        r = requests.post(f"{SERVER}/api/agent/poll", json={"token": TOKEN}, timeout=8)
        j = r.json()
        if not j.get("ok"):
            if not _warned_offline:
                print(f"⚠️ Server: {j.get('error')}")
                _warned_offline = True
            time.sleep(POLL)
            continue
        _warned_offline = False

        if j.get("config"):
            bs.bot_config.update(j["config"])

        desired = bool(j.get("desired_running"))
        running = _bot_running()

        if desired and not running and (time.time() - _last_start) > 15:
            start_bot()
        elif not desired and running:
            print("⏹ Stop z webu…")
            stop_bot()

    except requests.exceptions.RequestException as e:
        print(f"… spojenie so serverom zlyhalo, skúšam znova ({e.__class__.__name__})")
    except Exception as e:
        print(f"… chyba: {e}")

    report()
    time.sleep(POLL)
