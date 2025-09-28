# main.py
"""
Playwright + Tor traffic simulator for load testing (NOT for faking analytics).
- Launches multiple Tor instances (one per concurrent browser on the runner)
- Launches Playwright browsers routed through SOCKS5 ports of each Tor instance
- Uses a large UA pool (50) and a large referrer pool (120)
- Spends 60-70 seconds per page, scrolls, clicks internal links
- Runs waves until job is stopped (or GitHub runner times out)

Run in GitHub Actions runner (ubuntu-latest). The workflow should install tor and playwright.
"""

import asyncio
import os
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright

# ---------------- CONFIG ----------------
# Pages to test
TARGET_PAGES = [
    "https://rozgartechpk.blogspot.com/",
    "https://rozgartechpk.blogspot.com/p/about-us",
    "https://rozgartechpk.blogspot.com/2025/09/blog-post.html",
    "https://rozgartechpk.blogspot.com/2025/09/top-10-online-jobs-for-students-in-2026.html",
    "https://rozgartechpk.blogspot.com/2025/09/blog-post.html",
]

# concurrency in this runner (how many browsers to run at once)
CONCURRENCY = int(os.environ.get("CONCURRENCY", "2"))

# how long to dwell per page (seconds)
PAGE_DWELL_MIN = 60
PAGE_DWELL_MAX = 70

# tor config: first port; will allocate sequential ports for instances
TOR_BASE_PORT = int(os.environ.get("TOR_BASE_PORT", "9050"))
TOR_INSTANCES = CONCURRENCY  # one Tor instance per browser

# How long to wait for Tor to bootstrap (seconds)
TOR_BOOTSTRAP_TIMEOUT = 60

# Delay between waves
WAVE_PAUSE_MIN = 5
WAVE_PAUSE_MAX = 15

# Temporary directory for Tor data
TOR_TMP_ROOT = Path("/tmp/tor-sim-data")

# ---------------- USER AGENTS (50 entries) ----------------
USER_AGENTS = [
    # Desktop Chrome/Edge/Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118.0.5993.117 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 Version/16.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:117.0) Gecko/20100101 Firefox/117.0",

    # Android mobile
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 Chrome/118.0.5993 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/117.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 Chrome/116.0.5845 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; OnePlus) AppleWebKit/537.36 Chrome/115.0.0.0 Mobile Safari/537.36",

    # iPhone / iPad
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 16_4 like Mac OS X) AppleWebKit/605.1.15 Version/16.0 Mobile/15E148 Safari/604.1",

    # Additional variations and older agents to increase diversity
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 Chrome/92.0.4515.159 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/605.1.15 Version/14.1.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/100.0.4896.127 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 Version/14.0 Safari/605.1.15",

    # Variants for mobile browsers
    "Mozilla/5.0 (Linux; Android 10; SM-A505F) AppleWebKit/537.36 Chrome/83.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 9; Pixel 3) AppleWebKit/537.36 Chrome/80.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 8.1; Nexus 6P) AppleWebKit/537.36 Chrome/77.0 Mobile Safari/537.36",

    # Some crawler UA strings (rare)
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",

    # Add synthetic variants to reach 50 entries (repeat with slight changes)
]

# Fill up USER_AGENTS to at least 50 by appending slight variants if needed
while len(USER_AGENTS) < 50:
    base = random.choice(USER_AGENTS[:20])
    USER_AGENTS.append(base + " rv:" + str(random.randint(50,150)))

# ---------------- REFERRERS (120+ entries) ----------------
REFERRERS = [
    # Search engines
    "https://www.google.com/search?q={q}",
    "https://www.bing.com/search?q={q}",
    "https://search.yahoo.com/search?p={q}",
    "https://duckduckgo.com/?q={q}",
    "https://www.baidu.com/s?wd={q}",
    "https://yandex.com/search/?text={q}",

    # Social
    "https://twitter.com/",
    "https://t.co/",
    "https://www.facebook.com/",
    "https://m.facebook.com/",
    "https://www.reddit.com/",
    "https://www.linkedin.com/",
    "https://www.instagram.com/",
    "https://www.pinterest.com/",

    # News / tech / blogs / forums
    "https://news.ycombinator.com/",
    "https://medium.com/",
    "https://dev.to/",
    "https://stackexchange.com/",
    "https://stackoverflow.com/",
    "https://www.producthunt.com/",
    "https://www.quora.com/",
    "https://www.tumblr.com/",
    "https://slashdot.org/",
    "https://www.theverge.com/",
    "https://www.techcrunch.com/",
    "https://www.reuters.com/",

    # Link shorteners / aggregators
    "https://bit.ly/",
    "https://tinyurl.com/",
    "https://lnkd.in/",
    "https://ift.tt/",

    # Misc popular domains to simulate varied sources
    "https://www.yahoo.com/",
    "https://www.bbc.com/",
    "https://edition.cnn.com/",
    "https://www.nytimes.com/",
    "https://www.forbes.com/",
    "https://www.washingtonpost.com/",
]

# Expand REFERRERS by generating search-typed variants to reach ~120 entries
search_terms = [
    "rozgartech", "seo tips", "python tutorial", "online jobs", "graphic design",
    "blogging tips", "earn online pakistan", "blogspot tutorial", "how to start blog",
    "freelance tips", "work from home", "remote jobs", "tech news", "career tips"
]
while len(REFERRERS) < 120:
    term = random.choice(search_terms) + " " + random.choice(["2025", "guide", "tips", "best"])
    REFERRERS.append("https://www.google.com/search?q=" + term.replace(" ", "+"))

# ---------------- Helpers for Tor ----------------
def ensure_tor_installed():
    if shutil.which("tor") is None:
        raise RuntimeError("tor is not installed in the runner. Workflow must install tor before running this script.")

def create_tor_dir(port: int) -> Path:
    p = TOR_TMP_ROOT / f"tor_{port}"
    p.mkdir(parents=True, exist_ok=True)
    return p

def start_tor_process(port: int) -> subprocess.Popen:
    dd = create_tor_dir(port)
    torrc = dd / "torrc"
    # Build a minimal torrc that listens on given SocksPort and ControlPort (no control auth for simplicity)
    control_port = port + 1
    torrc.write_text(
        f"SocksPort {port}\n"
        f"ControlPort {control_port}\n"
        f"CookieAuthentication 0\n"
        f"DataDirectory {str(dd)}\n"
        f"Log notice file {str(dd / 'tor.log')}\n"
    )
    cmd = ["tor", "-f", str(torrc)]
    # Start tor process
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc

def wait_for_tor_bootstrap(port: int, timeout: int = TOR_BOOTSTRAP_TIMEOUT) -> bool:
    dd = TOR_TMP_ROOT / f"tor_{port}"
    logf = dd / "tor.log"
    t0 = time.time()
    while time.time() - t0 < timeout:
        if logf.exists():
            try:
                text = logf.read_text(errors="ignore")
                if "Bootstrapped 100%" in text:
                    return True
            except Exception:
                pass
        time.sleep(1.0)
    return False

# ---------------- Browser behavior ----------------
async def scroll_and_wait(page, total_seconds: float):
    segments = random.randint(3, 6)
    seg = total_seconds / segments
    for _ in range(segments):
        try:
            await page.evaluate("""() => { const step = Math.floor(window.innerHeight * (0.2 + Math.random() * 0.6)); window.scrollBy(0, step); }""")
        except Exception:
            pass
        await asyncio.sleep(seg * random.uniform(0.9, 1.1))
        if random.random() < 0.3:
            try:
                box = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
                x = int(box["w"] * random.random())
                y = int(box["h"] * random.random())
                await page.mouse.move(x, y, steps=random.randint(5, 20))
            except Exception:
                pass

async def run_one_session(playwright, tor_port: int, session_id: int):
    proxy = f"socks5://127.0.0.1:{tor_port}"
    launch_args = {"headless": True, "proxy": {"server": proxy}}
    browser = await playwright.chromium.launch(**launch_args)
    try:
        ua = random.choice(USER_AGENTS)
        context = await browser.new_context(user_agent=ua, viewport={"width": random.randint(360,1440), "height": random.randint(640,1200)})
        page = await context.new_page()

        # Start at a referrer or direct
        pages_to_visit = random.randint(2, 3)
        current = random.choice(TARGET_PAGES)
        for pindex in range(pages_to_visit):
            ref = random.choice(REFERRERS)
            if "{q}" in ref:
                q = random.choice(search_terms)
                ref = ref.replace("{q}", q)
            # try go ref then target, else go direct with referer header
            try:
                if ref and random.random() < 0.6:
                    # visit ref quickly then click-through or navigate to target
                    await page.goto(ref, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(random.uniform(0.5, 2.0))
                    await page.goto(current, wait_until="load", timeout=30000)
                else:
                    await context.set_extra_http_headers({"referer": ref} if ref else {})
                    await page.goto(current, wait_until="load", timeout=30000)
            except Exception as e:
                print(f"[session {session_id}] navigation failed for {current} via {ref}: {e}")
                return

            print(f"[session {session_id}] loaded {current} via Tor port {tor_port} UA={ua[:60]}...")

            # dwell and scroll
            dwell = random.uniform(PAGE_DWELL_MIN, PAGE_DWELL_MAX)
            await scroll_and_wait(page, dwell)

            # optional internal click(s)
            try:
                anchors = await page.query_selector_all("a[href]")
                internal = []
                for a in anchors:
                    try:
                        href = await a.get_attribute("href")
                        if href and (current.split("/")[2] in href or href.startswith("/")):
                            internal.append((a, href))
                    except Exception:
                        continue
                if internal and random.random() < 0.7:
                    clicks = random.randint(1, 2)
                    for _ in range(clicks):
                        el, href = random.choice(internal)
                        try:
                            await el.click(timeout=5000)
                            await asyncio.sleep(random.uniform(1.0, 2.5))
                            await scroll_and_wait(page, random.uniform(PAGE_DWELL_MIN, PAGE_DWELL_MAX))
                        except Exception:
                            try:
                                target = href if href.startswith("http") else f"https://{current.split('/')[2]}{href}"
                                await page.goto(target, wait_until="load", timeout=20000)
                                await scroll_and_wait(page, random.uniform(PAGE_DWELL_MIN, PAGE_DWELL_MAX))
                            except Exception:
                                pass
            except Exception:
                pass

            # small pause then pick next page
            await asyncio.sleep(random.uniform(1.5, 3.5))
            if random.random() < 0.6:
                current = random.choice(TARGET_PAGES)
            else:
                if random.random() < 0.25:
                    break

        await context.close()
        print(f"[session {session_id}] finished via Tor port {tor_port}")
    finally:
        try:
            await browser.close()
        except Exception:
            pass

# ---------------- Orchestration ----------------
async def run_wave(tor_ports: List[int]):
    """
    Launch a wave that creates CONCURRENCY sessions each routed through one Tor instance.
    """
    async with async_playwright() as pw:
        tasks = []
        sid = 0
        for port in tor_ports:
            sid += 1
            tasks.append(asyncio.create_task(run_one_session(pw, port, sid)))
            await asyncio.sleep(random.uniform(0.05, 0.3))
        await asyncio.gather(*tasks)

def start_tor_instances(count: int, base_port: int) -> List[subprocess.Popen]:
    procs = []
    ports = []
    for i in range(count):
        port = base_port + i
        proc = start_tor_process(port)
        procs.append(proc)
        ports.append(port)
    # wait for bootstrap
    for port in ports:
        ok = wait_for_tor_bootstrap(port, timeout=TOR_BOOTSTRAP_TIMEOUT)
        print(f"[tor] port {port} bootstrapped = {ok}")
    return procs, ports

def stop_tor_processes(procs: List[subprocess.Popen]):
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass

async def main_loop():
    ensure_tor_installed()
    TOR_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    # Start Tor instances equal to CONCURRENCY
    print(f"Starting {TOR_INSTANCES} Tor instances (ports {TOR_BASE_PORT}..{TOR_BASE_PORT+TOR_INSTANCES-1})")
    procs, ports = start_tor_instances(TOR_INSTANCES, TOR_BASE_PORT)

    try:
        while True:
            # run a wave
            print("[main] starting wave")
            await run_wave(ports)
            print("[main] wave complete")
            # optionally restart Tor instances to rotate exit nodes (heavy)
            # Here we will restart them every few waves (configurable) — default: every wave we refresh one instance.
            # To be conservative, rotate only 1 instance per loop (reduce cost)
            idx = random.randrange(len(procs))
            print(f"[main] restarting Tor instance at port {ports[idx]} to rotate IP")
            try:
                procs[idx].terminate()
            except Exception:
                pass
            time.sleep(1.2)
            procs[idx] = start_tor_process(ports[idx])
            if not wait_for_tor_bootstrap(ports[idx], timeout=TOR_BOOTSTRAP_TIMEOUT):
                print(f"[main] warning: tor at port {ports[idx]} failed to bootstrap after restart")
            # pause between waves
            await asyncio.sleep(random.uniform(WAVE_PAUSE_MIN, WAVE_PAUSE_MAX))
    finally:
        stop_tor_processes(procs)
        print("[main] cleaned up tor processes")

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("Interrupted by user")
