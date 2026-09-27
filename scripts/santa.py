#!/usr/bin/env python3
"""
santa.py | The Santa Skill pipeline. Christmas versions of Amazon secondary images.

Subcommands (all take the run folder as the last argument, default ./santa/<ASIN>):

  fetch  <ASIN|URL|folder> [--out DIR]   download listing images (or copy a folder) to originals/
  list   <DIR>                            print originals with size and aspect (for the agent to classify)
  classify <DIR>                          vision pass (gpt-5.4): fills plan.json role/action/scene
  generate <DIR> [--only NAME]           run gpt-image-2 images.edit on every "transform" entry in plan.json
  regen  <DIR> <NAME> [--note TEXT]      delete one output and generate it again (the one allowed retry)
  check  <DIR>                            mean brightness before vs after per image (flags a drop over 8%)
  verify <DIR>                            vision pass: before/after judged on the rules, writes verdicts
  sheet  <DIR>                            write contact-sheet.html and report.md from plan.json + cost log

Catalog mode (a whole store in one command) lives in catalog.py beside this file and
calls the functions below per ASIN.

Env:
  OPENAI_API_KEY       required for generate/regen/classify/verify (fallback: ~/Downloads/Claude/.env)
  QUALITY              medium (default, ready to upload) | low (a cheap try) | high (costs 3.4x, no visible gain)
  WORKERS              parallel workers, default 4
  SANTA_VISION_MODEL   the model for classify/verify, default gpt-5.4

Rules the prompt is built from: ../references/christmas-prompt-rules.md
No retries against Amazon. No retries against OpenAI. One attempt per image, by design.
"""
import argparse
import base64
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES_FILE = HERE.parent / "references" / "christmas-prompt-rules.md"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

VISION_MODEL = os.getenv("SANTA_VISION_MODEL", "gpt-5.4")
# USD per 1M tokens (in, out) for the vision estimate. gpt-5.4 was measured consistent on
# the same gallery twice (26.9.2026); mini flipped one lifestyle frame to infographic between runs.
VISION_RATES = {"gpt-5.4": (2.5, 15.0), "gpt-5.4-mini": (0.75, 4.5), "gpt-5.4-nano": (0.2, 1.2),
                "gpt-5.5": (5.0, 30.0)}


def vision_cost(usage):
    rin, rout = VISION_RATES.get(VISION_MODEL, (2.5, 15.0))
    return round(usage.prompt_tokens * rin / 1e6 + usage.completion_tokens * rout / 1e6, 5)

# Jay's STRONG Christmas (decided 5.9.2026 for the Cyprus talk after a 10-image test: the subtle
# version disappeared at Amazon thumbnail size) and his WINTER conversion for summer scenes. These
# are the prompts that made the Cyprus slides; they reached the skill on 26.9.2026 (Jay: "I want it
# to do these things"). KEEP is the part that never moves.
KEEP = (
    "Keep the product exactly as it is: same shape, color, size, position, angle and label, nothing placed on it, "
    "in front of it or touching it. Keep every existing headline, label and graphic element exactly as written, "
    "same position. Keep every person's face, identity, pose, hands and expression exactly the same. Keep the "
    "camera angle, framing and lens. Photorealistic, matching lighting and shadows, no cartoon items, no glitter, "
    "no floating objects, no added text or logos. The result must be as bright as the original or brighter, warm "
    "and festive."
)
STRONG_TEMPLATE = (
    "Create a rich, unmistakable Christmas version of this exact image, one that reads as Christmas even as a "
    "small thumbnail. Add one large festive anchor plus several supporting elements: {scene} "
    "Use warm string lights generously. Clothing stays unchanged. At most one natural red Santa hat, on one "
    "adult only, never on a child, matching head shape, shadow and lighting. " + KEEP
)
WINTER_TEMPLATE = (
    "Turn this summer scene into a full Christmas winter scene while keeping it the same photo of the same "
    "product and the same people. Season change: bright overcast winter light, fresh snow on the ground and on "
    "outdoor surfaces, bare or snow-dusted trees and plants. Change the clothing to warm winter clothing that fits "
    "each person naturally (knit sweaters, jackets, beanies, scarves, boots), same body positions. Summer drinks "
    "and props that are not the product may become winter ones. Add Christmas: {scene} At most one natural red "
    "Santa hat, on one adult only, never on a child. " + KEEP
)
# Christmas in the sun (Jay, 27.9.2026): people in a pool or on a beach wearing coats in the snow is absurd.
# Water, a pool, a beach or swimwear keep the warm setting and the same clothes; the Christmas comes to them.
SUN_TEMPLATE = (
    "Create a rich, unmistakable Christmas-in-the-sun version of this exact image, one that reads as Christmas "
    "even as a small thumbnail. This is a warm-weather water scene, so the warm setting stays: the same sunshine, "
    "the same water, sand or pool deck, and the same swimwear and summer clothes on the same people. No snow, no "
    "frost, no winter clothing. Add a Christmas that belongs in the sun: {scene} Use warm string lights "
    "generously, with red and gold accents. At most one natural red Santa hat, on one adult only, never on a "
    "child, matching head shape, shadow and lighting. " + KEEP
)
TEMPLATES = {"scene": STRONG_TEMPLATE, "winter": WINTER_TEMPLATE, "sun": SUN_TEMPLATE}
# The code check behind the classifier's "sun" call: a winter pick whose picture holds water or swimwear.
WATER_WORDS = re.compile(r"\b(pools?|poolside|beach(es)?|bikinis?|swim\w*|ocean|sea|seaside|surf(ing|er|ers|board)?)\b", re.I)

# Rough per-token rates used only for the cost ESTIMATE in report.md.
# Published gpt-image-1 rates; gpt-image-2 is billed the same way (tokens in, image tokens out).
# The real number is on the OpenAI usage page. The script prints the token counts it was billed.
RATE_TEXT_IN = 5.0 / 1_000_000
RATE_IMAGE_IN = 10.0 / 1_000_000
RATE_IMAGE_OUT = 40.0 / 1_000_000
# Per-image planning estimates, measured 26.9.2026 on the same four frames at each quality
# (usage tokens x published rates): low 196 output tokens / 15 s, medium 1,756 / 41 s, high 7,024 / 118 s.
EST_PER_IMAGE = {"low": 0.025, "medium": 0.087, "high": 0.298}
SECONDS_PER_IMAGE = {"low": 15, "medium": 41, "high": 118}
# One run at medium, ready to upload (Jay, 26.9.2026). Low renders drawn elements (a Santa hat,
# skin) visibly waxier; high looked the same as medium at 3.4x the price and 3x the time. And a
# second run at another quality is a NEW image, not a render of the approved one, so there is no
# preview-then-final flow.
DEFAULT_QUALITY = "medium"
# Fallback flat rates per output image when the API returns no usage block.
FLAT_PER_IMAGE = EST_PER_IMAGE


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def asin_from(text):
    m = re.search(r"(?:/dp/|/gp/product/|/gp/aw/d/|/product/)([A-Z0-9]{10})", text)
    if m:
        return m.group(1)
    m = re.fullmatch(r"[A-Z0-9]{10}", text.strip())
    return m.group(0) if m else None


# ---------------------------------------------------------------- product references
# What people paste: a bare ASIN, a product link from any Amazon marketplace (with the
# tracking junk after it), or a share link from the app (amzn.to / a.co). One ASIN each.
AMAZON_HOST = re.compile(r"^(?:www\.|smile\.|m\.)?(amazon\.(?:com|ca|com\.mx|com\.br|co\.uk|de|fr|it|es|nl|se|pl|"
                         r"com\.be|ie|com\.tr|ae|sa|eg|in|co\.jp|sg|com\.au))$")
SHORT_HOSTS = {"amzn.to", "a.co", "amzn.eu", "amzn.asia"}
BARE_ASIN = re.compile(r"\b(?:B0[A-Z0-9]{8}|\d{9}[\dX])\b")
LINK = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)*(?:amazon\.[a-z.]{2,9}|amzn\.to|a\.co|amzn\.eu|amzn\.asia)/[^\s,;\"'<>)]+", re.I)


def resolve_short(url):
    """One HEAD request, no redirect follow: the first Location header already holds /dp/ASIN
    (measured on six amzn.to links, 26.9.2026). Returns the Location or ""."""
    r = subprocess.run(["curl", "-sI", "--max-time", "15", "-A", UA, url], capture_output=True)
    m = re.search(r"^location:\s*(\S+)", r.stdout.decode("utf-8", "ignore"), re.I | re.M)
    return m.group(1) if m else ""


def product_ref(text, resolve=True):
    """(asin, host) for one pasted item, or (None, reason)."""
    t = text.strip().strip("<>\"'")
    if re.fullmatch(r"[A-Z0-9]{10}", t):
        return t, "www.amazon.com"
    url = t if re.match(r"https?://", t, re.I) else "https://" + t
    m = re.match(r"https?://([^/?#]+)", url, re.I)
    host = m.group(1).lower() if m else ""
    if host in SHORT_HOSTS:
        if not resolve:
            return None, "short link that did not resolve"
        loc = resolve_short(url)
        return product_ref(loc, resolve=False) if loc else (None, "short link that did not resolve")
    hm = AMAZON_HOST.match(host)
    if not hm:
        return None, "not an Amazon link"
    if re.search(r"/stores/|/shop/|/s\?|[?&]me=", url):
        return None, "a store or search link, not a product"
    a = asin_from(url)
    return (a, "www." + hm.group(1)) if a else (None, "no ASIN in the link")


def refs_from_text(text, resolve=True):
    """Every product in a pasted blob or file, in order, deduplicated.
    Returns ([(asin, host)], [(item, reason)])."""
    found, bad, seen = [], [], set()
    spans = []
    for m in LINK.finditer(text):
        spans.append((m.start(), m.group(0)))
    covered = [(m.start(), m.end()) for m in LINK.finditer(text)]
    for m in re.finditer(r"https?://[^\s,;\"'<>)]+", text, re.I):   # any other link: reported, never mined
        if not any(a <= m.start() < b for a, b in covered):
            spans.append((m.start(), m.group(0)))
            covered.append((m.start(), m.end()))
    for m in BARE_ASIN.finditer(text):
        if not any(a <= m.start() < b for a, b in covered):
            spans.append((m.start(), m.group(0)))
    spans.sort()
    shorts = 0
    for _, item in spans:
        if resolve and any(h in item.lower() for h in SHORT_HOSTS) and shorts:
            time.sleep(0.5)
        shorts += any(h in item.lower() for h in SHORT_HOSTS)
        a, host = product_ref(item, resolve=resolve)
        if not a:
            bad.append((item, host))
        elif a not in seen:
            seen.add(a)
            found.append((a, host))
    return found, bad


def load_key():
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key
    for env in (Path.cwd() / ".env", Path.home() / "Downloads/Claude/.env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# A hung request must not hang a 300-product run: measured 26.9.2026, four vision calls sat open
# for five minutes under the SDK default (600 s, two silent retries). Edits keep the one-attempt
# rule, so no SDK retry either; a vision call may retry once, it costs a fraction of a cent.
TIMEOUTS = {"edit": 180, "vision": 60}


def openai_client(kind="edit"):
    key = load_key()
    if not key:
        sys.exit("OPENAI_API_KEY missing. export OPENAI_API_KEY=sk-... or put it in ./.env or ~/Downloads/Claude/.env")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("openai package missing. Fix: python3 -m pip install openai")
    return OpenAI(api_key=key, timeout=TIMEOUTS[kind], max_retries=0 if kind == "edit" else 1)


def image_size(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def api_size_for(w, h):
    if w == 0 or h == 0:
        return "1024x1024"
    r = w / h
    if r > 1.2:
        return "1536x1024"
    if r < 0.83:
        return "1024x1536"
    return "1024x1024"


def read_plan(run):
    p = run / "plan.json"
    if not p.exists():
        sys.exit(f"plan.json missing in {run}. Run `list` then `classify` (or fill it by hand).")
    return json.loads(p.read_text())


def write_plan(run, plan):
    (run / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))


def append_cost(run, entry):
    p = run / "cost.jsonl"
    with open(p, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_costs(run):
    p = run / "cost.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def originals_of(run):
    d = run / "originals"
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))


# ---------------------------------------------------------------- fetch
def curl(url, out=None, timeout=30):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA,
           "-H", "Accept-Language: en-US,en;q=0.9",
           "-H", "Accept: text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
           "--compressed", url]
    if out:
        cmd += ["-o", str(out)]
        r = subprocess.run(cmd, capture_output=True)
        return r.returncode == 0
    r = subprocess.run(cmd, capture_output=True)
    return r.stdout.decode("utf-8", "ignore") if r.returncode == 0 else ""


def parse_listing(page):
    title = ""
    m = re.search(r'id="productTitle"[^>]*>\s*(.*?)\s*<', page, re.S)
    if m:
        title = html.unescape(m.group(1)).strip()
    urls = []
    for key in ("hiRes", "large"):
        for u in re.findall(r'"%s"\s*:\s*"(https://[^"]+)"' % key, page):
            u = u.replace("\\/", "/")
            if ("images-amazon" in u or "media-amazon" in u) and u not in urls:
                urls.append(u)
        if urls:
            break
    # keep only product gallery images (m.media-amazon.com/images/I/...), drop video thumbs / sprites
    urls = [u for u in urls if "/images/I/" in u and not u.endswith(".gif")]
    return title, urls


def blocked(page):
    """Captcha, the Akamai interstitial (a 2 KB page with bm-verify), or an empty answer."""
    if not page or len(page) < 20000:
        return True
    low = page.lower()
    return ("api-services-support@amazon.com" in page) or ("type the characters you see" in low) or \
        ("bm-verify" in page) or ("_sec/verify" in page) or ("captcha" in low and "productTitle" not in page)


def fetch_listing(asin, run, keep_page=True, host="www.amazon.com"):
    """One attempt at the listing page, then the gallery. Returns (status, n_images):
    status is "ok", "blocked" (captcha or empty page) or "few" (under 2 usable images)."""
    originals = run / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    url = f"https://{host}/dp/{asin}"
    log(f"Fetching {url} (one attempt, no retries)")
    page = curl(url)
    if keep_page:
        (run / "page.html").write_text(page)
    if blocked(page):
        return "blocked", 0
    title, urls = parse_listing(page)
    listing = {"asin": asin, "title": title, "source": url, "image_urls": urls}
    (run / "listing.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False))
    log(f"Title: {title[:90]}")
    log(f"Found {len(urls)} image URLs")
    if len(urls) < 2:
        return "few", len(urls)
    ok = 0
    for i, u in enumerate(urls, 1):
        ext = ".png" if u.lower().endswith(".png") else ".jpg"
        dest = originals / f"{i:02d}{ext}"
        if dest.exists() and dest.stat().st_size > 5000:
            ok += 1
            continue
        good = curl(u, dest, timeout=60) and dest.exists() and dest.stat().st_size > 5000
        if good:
            ok += 1
            log(f"  {dest.name}  {dest.stat().st_size // 1024} KB")
        else:
            log(f"  {dest.name}  FAILED (not retrying)")
            if dest.exists():
                dest.unlink()
    log(f"Downloaded {ok}/{len(urls)} to {originals}")
    return ("ok" if ok >= 2 else "few"), ok


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def folder_images(src_path):
    """The images in a product folder, in gallery order (the file names sort as the gallery: 01 is main)."""
    return [p for p in sorted(src_path.iterdir()) if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")]


def copy_folder(src_path, run, asin=None, title=None):
    """A folder of product images instead of an Amazon fetch: copy to originals/, write listing.json.
    Returns the number of images copied."""
    originals = run / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    files = folder_images(src_path)
    for p in files:
        shutil.copy(p, originals / p.name)
    listing = {"asin": asin or src_path.name, "title": title or src_path.name,
               "source": "folder", "folder": str(src_path), "images": len(files),
               "image_files": [p.name for p in files]}
    (run / "listing.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False))
    log(f"Copied {len(files)} images from {src_path} to {originals}")
    return len(files)


def cmd_fetch(args):
    src = args.source
    out = Path(args.out) if args.out else None
    src_path = Path(os.path.expanduser(src))
    if src_path.is_dir():
        run = out or Path.cwd() / "santa" / src_path.name
        n = copy_folder(src_path, run, title=args.title)
        if n < 2:
            sys.exit("Fewer than 2 images. Nothing to transform beyond a main image.")
        return

    asin, host = product_ref(src)
    if not asin:
        sys.exit(f"Not an ASIN, an Amazon product link or a folder ({host}): {src}")
    run = out or Path.cwd() / "santa" / asin
    status, n = fetch_listing(asin, run, host=host)
    if status == "blocked":
        log("")
        log("AMAZON FETCH BLOCKED OR EMPTY.")
        log("Amazon returned a captcha or nothing. This skill will not retry (retries get IPs blocked).")
        log("Fallback: save the listing images to a folder and re-run with that folder:")
        log(f"  python3 {HERE / 'santa.py'} fetch /path/to/images --out {run}")
        sys.exit(2)
    if status == "few":
        log("Fewer than 2 gallery images found. Use the folder fallback:")
        log(f"  python3 {HERE / 'santa.py'} fetch /path/to/images --out {run}")
        sys.exit(2)


# ---------------------------------------------------------------- list
def ensure_plan(run):
    """Write the plan.json template if it is missing. Returns the plan."""
    if (run / "plan.json").exists():
        return read_plan(run)
    listing = {}
    lp = run / "listing.json"
    if lp.exists():
        listing = json.loads(lp.read_text())
    plan = {"asin": listing.get("asin", run.name), "title": listing.get("title", ""),
            "images": [{"file": p.name, "role": "TODO", "action": "TODO", "scene": "", "reason": ""}
                       for p in originals_of(run)]}
    write_plan(run, plan)
    return plan


def cmd_list(args):
    run = Path(args.run)
    for p in originals_of(run):
        w, h = image_size(p)
        log(f"{p.name}  {w}x{h}  -> {api_size_for(w, h)}")
    if not (run / "plan.json").exists():
        ensure_plan(run)
        log(f"\nWrote plan.json template. Fill role (main|lifestyle|packshot|infographic|logo), "
            f"action (transform|skip), scene, reason. Or run `classify` to let the vision pass fill it.")


# ---------------------------------------------------------------- vision (classify / verify)
def _img_b64(path, max_px=768):
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def vision_json(client, prompt, images, max_px=768):
    """One chat call with text + images, JSON object back. Returns (dict, usage) or (None, None)."""
    content = [{"type": "text", "text": prompt}]
    for p in images:
        content.append({"type": "image_url", "image_url": {"url": _img_b64(p, max_px), "detail": "high"}})
    r = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )
    text = r.choices[0].message.content or "{}"
    try:
        return json.loads(text), r.usage
    except json.JSONDecodeError:
        return None, r.usage


CLASSIFY_PROMPT = """You classify ONE Amazon product listing image for a Christmas-edition workflow.
Product title: "{title}"
This is image {index} of {total} in the gallery. Image 01 is the main image and is never transformed.

Decide role, action, and (for transforms) a scene line for gpt-image-2. Go through these in order:
1. Two or more separate photos tiled in one frame (panels, split screen, grid), or two or more color
   versions of the product side by side? role collage, SKIP.
2. The subject is a brand logo card, a retail box on its own, or a brand graphic (a logo and a claim on a
   flat colored background, the product only a cut-out decoration at the edge)? role logo, SKIP.
3. Image 2 is the whole gallery as a numbered strip, for context only; judge image 1. If image 1 is the
   product on plain white in a DIFFERENT color or finish than the main image (01 in the strip), it is the
   main image of a sibling listing: role variant, SKIP, reason "color variant".
3b. Any other image of the product on a plain white or plain studio background, with or without
   props, labels or badges: role packshot, SKIP, reason "product on white". Santa dresses lifestyle
   images only.
4. Count the separate text elements (a headline and its sub-line count as two). Text in a colored band
   above or below the photo, or on a plain area beside it, is NOT on the product. It is an infographic
   ONLY if one of these is true: five or more text elements; feature bullets, spec tables, dimension
   arrows, diagrams or comparison charts; or text printed across the product's own body. Then role
   infographic, SKIP, and the reason says which of the three.
5. Everything else is a lifestyle photo that can take Christmas: the product in a real setting, people
   or not, with or without a headline band. role lifestyle, TRANSFORM. A headline plus a
   sub-line is the normal Amazon lifestyle frame; gpt-image-2 keeps short text intact and a later pass
   reads it back. When you hesitate between lifestyle and infographic, choose lifestyle.
- mode, for every transform: "sun" when people are in the water, at a pool or on a beach, or in swimwear
  (a pool, a beach, a swim spot, even with nobody in the water): snow and coats there are absurd, so the
  warm setting and the clothes stay and Christmas comes in the sun. "sun" wins over "winter". "winter"
  for any other summer or warm-weather scene (outdoors in sun, a patio, a backyard, people in summer
  clothes), which becomes a full winter scene with snow and winter clothing; "scene" for everything else.
  Set summer=true with "winter" only.
- Scene line: what gets added and WHERE, anchored to what is IN this picture. The treatment is RICH:
  it must read as Christmas at thumbnail size.
  "scene": ONE large anchor plus 3 or 4 supporting elements. Anchor: a tall, fully decorated Christmas
  tree with warm lights in any home interior; elsewhere a big lit wreath, a lit garland along a shelf,
  railing or fence, or a small decorated evergreen outdoors. Supporting: lit garland, wrapped gift boxes
  on the floor or a far surface, stockings on the wall, a wreath, warm string lights, snow falling outside
  a window, a light dusting of snow on outdoor ground. Name where each one goes (back right beside the
  window, on the floor to the far left). Never on, in front of or touching the product or anyone's hands.
  "winter": the Christmas additions (lights along the fence, a small decorated evergreen in the corner,
  gifts on the far side) plus anything product-specific that must stay ("Keep the drinks on the cooler
  lid").
  "sun": a Christmas that belongs in the heat: warm string lights along the umbrella, railing or fence, a
  small decorated palm or Christmas tree on the deck or sand, tinsel, wrapped gifts on the far side, red and
  gold accents. Never snow, never winter clothing.
  Santa hat: name the adult who gets it ("One natural red Santa hat on the father only"), or write
  "No Santa hat (children only)" when only children appear. Never more than one.
  Say what must stay exactly ("Keep the dog and the gray dog bed exactly as they are, nothing on the bed.").
  If the image carries a headline or labels, START with: Keep the headline '...' and the labels exactly as
  written, same position.
  Good: "A fully decorated Christmas tree with warm lights clearly visible behind the sofa on the right, a
  lit garland with bows along the windowsill, three wrapped gift boxes on the floor to the far left, and
  snow falling outside the window. Keep the dog and the gray dog bed exactly as they are, nothing on the bed."
  Good: "Warm string lights along the whole edge of the orange tent and between the trees above, a large
  evergreen wreath with a red bow on the tent door, three wrapped gifts beside the tent, a light dusting of
  snow on the ground. One natural red Santa hat on the father only. Keep the tent orange."
  Bad: "make it Christmassy" (no anchor), "add a red glow" (a filter), "put a bow on it" (touches the
  product), "Santa hats on everyone".
- seen: one line saying what is in the picture (written from the picture, not the file name).
- reason: for skips, why, in five words or fewer.

Return JSON only:
{{"seen": "...", "role": "...", "action": "transform|skip", "mode": "scene|winter|sun", "scene": "...",
  "reason": "...", "summer": false, "text_on_image": "the headline/labels you can read, or empty"}}"""


def gallery_strip(run, px=150):
    """All originals in one numbered strip, so the classifier knows the gallery (colour variants)."""
    from PIL import Image, ImageDraw
    files = originals_of(run)
    cols = min(len(files), 8)
    rows = (len(files) + cols - 1) // cols
    strip = Image.new("RGB", (cols * px, rows * (px + 16)), "white")
    dr = ImageDraw.Draw(strip)
    for k, f in enumerate(files):
        with Image.open(f) as im:
            im = im.convert("RGB")
            im.thumbnail((px - 6, px - 6))
            x, y = (k % cols) * px, (k // cols) * (px + 16)
            strip.paste(im, (x + 3, y + 16))
        dr.text((x + 4, y + 2), f.stem, fill="black")
    path = run / "gallery-strip.jpg"
    strip.save(path, quality=80)
    return path


def cmd_classify(args):
    """Vision pass: fill plan.json from the pictures. 01 is forced to main/skip."""
    run = Path(args.run)
    plan = ensure_plan(run)
    client = openai_client("vision")
    title = plan.get("title", "")
    imgs = plan["images"]
    total = len(imgs)
    todo = [(i, img) for i, img in enumerate(imgs)
            if args.force or img.get("action") in ("TODO", "", None)]
    if not todo:
        log("plan.json already classified (use --force to redo).")
        return
    workers = int(os.getenv("WORKERS", "4"))
    strip = gallery_strip(run)
    log(f"Classifying {len(todo)} images with {VISION_MODEL}")

    def one(i, img):
        src = run / "originals" / img["file"]
        if i == 0:
            return i, {"seen": "main image (first in gallery)", "role": "main", "action": "skip",
                       "scene": "", "reason": "main image stays white", "summer": False}
        prompt = CLASSIFY_PROMPT.format(title=title, index=img["file"], total=total)
        try:
            d, usage = vision_json(client, prompt, [src, strip])
        except Exception as e:
            log(f"  {img['file']}: classify FAILED {str(e)[:160]}")
            return i, None
        if usage:
            append_cost(run, {"file": img["file"], "op": "classify", "model": VISION_MODEL,
                              "input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens,
                              "usd_est": vision_cost(usage)})
        return i, d

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(one, i, img) for i, img in todo]):
            i, d = fut.result()
            img = imgs[i]
            if not d:
                img.update({"role": "unknown", "action": "skip", "reason": "classifier failed"})
                continue
            action = d.get("action", "skip")
            if action not in ("transform", "skip"):
                action = "skip"
            img.update({"seen": d.get("seen", ""), "role": d.get("role", ""), "action": action,
                        "scene": d.get("scene", "") if action == "transform" else "",
                        "reason": d.get("reason", "") if action == "skip" else "",
                        "summer": bool(d.get("summer")), "text_on_image": d.get("text_on_image", ""),
                        "mode": d.get("mode", "")})
            if action == "transform":
                img["mode"] = prompt_mode(img if img["mode"] in TEMPLATES else {**img, "mode": ""})
                if img["mode"] == "winter" and WATER_WORDS.search(img["seen"]):
                    # a pool, a beach or swimwear never gets snow and coats (Jay, 27.9.2026). Enforced here
                    # too, and the winter line's snow is dropped so it cannot argue with the sun template.
                    img.update({"mode": "sun", "summer": False,
                                "scene": re.sub(r"(,\s*(and\s+)?|\s+and\s+)?[^,.]*\bsnow\w*[^,.]*", "", img["scene"])})
            else:
                img["mode"] = ""
            if action == "transform" and not img["scene"].strip():
                img.update({"action": "skip", "reason": "no scene line"})
            if img["action"] == "transform" and img["role"] in ("packshot", "packshot-tight", "variant", "main"):
                # Santa dresses lifestyle images only (Jay, 26.9.2026): never the product on white,
                # never the main image, never an infographic. Enforced here, not left to the model.
                img.update({"action": "skip", "scene": "", "mode": "", "reason": "product on white"})
            log(f"  {img['file']}  {img['role']:<14} {img['action']:<9} {img.get('mode', ''):<8} {img.get('reason') or img['scene'][:60]}")
    write_plan(run, plan)
    n_t = sum(1 for i in imgs if i.get("action") == "transform")
    log(f"{n_t} to transform, {total - n_t} skipped. plan.json written.")


VERIFY_PROMPT = """You are the quality gate for a Christmas-edition Amazon listing image.
Image A is the ORIGINAL. Image B is the generated Christmas version. The product is the thing
being sold; Christmas may happen AROUND it, never TO it. Text that was on the original: "{text}"
What the original shows (from the classifier): "{seen}". Mode: "{mode}".

The treatment is meant to be RICH, so do not fail an image for having a lot of Christmas: a large
anchor (a lit tree in any home interior, a big wreath, lit garlands) plus several gifts, stockings and
string lights is correct. In mode "winter" a summer scene becomes full winter: snow outdoors and warm
winter clothing on the same people in the same poses is correct. In mode "sun" (people in the water, at
a pool or on a beach, or in swimwear) the warm setting and the swimwear stay: any snow, frost or winter
clothing there is a failure.

Answer each question about B compared to A:
1. same_product: identical shape, color, angle, size, label text, packaging? (drift = false)
2. nothing_touching: nothing ADDED on, in front of, or touching the product, and the product is not
   hidden? (Things that were already on it in A, like a drink on a cooler lid, stay and are fine.)
3. not_darker: B is as bright as A or brighter overall, no moody/dim look? (warmer is fine; a bright
   overcast winter sky is fine)
4. no_tint: no red/green filter over the whole image?
5. decor_ok: every added thing rests on a real surface or hangs naturally (a wall, a window, a fence, a
   tree), nothing floating?
6. people_ok: same faces, identity, poses, hands and expressions; clothing unchanged except, in mode
   "winter", summer clothes turned into winter clothes; at most one Santa hat (the red hat with white
   fur trim and a pompom), on an adult, never a child? A knit beanie or any other winter hat is
   clothing, not a Santa hat.
7. text_ok: every headline and label from A still present and spelled the same, no invented text or logos?
8. no_banned: no glitter, no cartoon items, no sparkle or snow overlay pasted flat over the photo? In mode
   "sun", no snow or frost anywhere?
9. christmas_obvious: would a shopper see "Christmas" at a glance at thumbnail size?
stage_ready is true only if ALL nine are true. Write a one or two sentence verdict saying what
changed and what, if anything, drifted. If not ready, write regen_note: one sentence of instruction
for the second attempt that names the failure (e.g. "The saw must stay blue. Add a lit tree behind
the sofa so it reads as Christmas.").

Return JSON only:
{{"same_product": true, "nothing_touching": true, "not_darker": true, "no_tint": true, "decor_ok": true,
  "people_ok": true, "text_ok": true, "no_banned": true, "christmas_obvious": true, "stage_ready": true,
  "verdict": "...", "regen_note": ""}}"""


FRAMING_MIN_SCALE = 0.88   # measured 26.9.2026: shrunk frames read 0.62-0.80, intact ones 0.97-1.0
FRAMING_MAX_SHIFT = 0.12   # centre moved by more than 12 percent of the width


def framing_check(orig, out, W=320):
    """Find the original's product (or centre) inside the output at 55-120 percent scale.
    Returns {"scale", "shift", "score"} or None when OpenCV is missing. The model cannot judge
    size: gpt-5.4 passed two of three frames whose product had shrunk by a quarter."""
    try:
        import numpy as np
        import cv2
        from PIL import Image
    except ImportError:
        return None
    a = np.array(Image.open(orig).convert("L"))
    b = np.array(Image.open(out).convert("L"))
    h = round(a.shape[0] * W / a.shape[1])
    a = cv2.resize(a, (W, h), interpolation=cv2.INTER_AREA)
    b = cv2.resize(b, (W, h), interpolation=cv2.INTER_AREA)  # same frame after pad-and-crop
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    ys, xs = np.where(np.abs(a.astype(int) - np.median(border)) > 25)
    if len(xs) and (xs.max() - xs.min()) * (ys.max() - ys.min()) < 0.85 * W * h:
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()       # a product on a plain ground
    else:
        x0, x1, y0, y1 = int(W * .2), int(W * .8), int(h * .2), int(h * .8)  # a full scene: its centre
    t = a[y0:y1 + 1, x0:x1 + 1]
    best = (-1.0, 1.0, 0.0, 0.0)
    for s in np.arange(0.55, 1.21, 0.025):
        tw, th = round(t.shape[1] * s), round(t.shape[0] * s)
        if tw < 12 or th < 12 or tw > W or th > h:
            continue
        r = cv2.matchTemplate(b, cv2.resize(t, (tw, th)), cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(r)
        if mx > best[0]:
            best = (mx, s, loc[0] + tw / 2, loc[1] + th / 2)
    score, s, cx, cy = best
    shift = ((cx - (x0 + x1) / 2) ** 2 + (cy - (y0 + y1) / 2) ** 2) ** .5 / W
    return {"scale": round(float(s), 3), "shift": round(float(shift), 3), "score": round(float(score), 2)}


def verify_pairs(run, only=None, force=False):
    """Vision verdicts for every generated transform without one (or `only`). Returns list of failing files."""
    plan = read_plan(run)
    client = openai_client("vision")
    workers = int(os.getenv("WORKERS", "4"))
    jobs = []
    for img in plan["images"]:
        if img.get("action") != "transform":
            continue
        if only and img["file"] != only:
            continue
        dest = run / "christmas" / (Path(img["file"]).stem + ".png")
        if not dest.exists():
            continue
        if img.get("verdict") and not only and not force:
            continue
        jobs.append(img)
    if not jobs:
        return []
    log(f"Verifying {len(jobs)} pairs with {VISION_MODEL}")

    def one(img):
        src = run / "originals" / img["file"]
        dest = run / "christmas" / (Path(img["file"]).stem + ".png")
        prompt = VERIFY_PROMPT.format(text=img.get("text_on_image", ""), seen=img.get("seen", ""),
                                      mode=prompt_mode(img))
        try:
            d, usage = vision_json(client, prompt, [src, dest])
        except Exception as e:
            log(f"  {img['file']}: verify FAILED {str(e)[:160]}")
            return img, None
        if usage:
            append_cost(run, {"file": img["file"], "op": "verify", "model": VISION_MODEL,
                              "input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens,
                              "usd_est": vision_cost(usage)})
        return img, d

    failing = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(one, img) for img in jobs]):
            img, d = fut.result()
            if not d:
                img["verify_error"] = "no answer from the verifier; the next run tries again"
                continue
            img.pop("verify_error", None)
            keys = ("same_product", "nothing_touching", "not_darker", "no_tint", "decor_ok",
                    "people_ok", "text_ok", "no_banned", "christmas_obvious")
            checks = {k: bool(d.get(k, False)) for k in keys}
            verdict = d.get("verdict", "")
            note = d.get("regen_note", "")
            fr = framing_check(run / "originals" / img["file"], run / "christmas" / (Path(img["file"]).stem + ".png"))
            if fr is not None:
                img["framing"] = fr
                moved = fr["score"] >= 0.5 and (fr["scale"] < FRAMING_MIN_SCALE or fr["shift"] > FRAMING_MAX_SHIFT)
                checks["same_framing"] = not moved
                if moved:
                    verdict = (f"Product {'shrank to ' + str(round(fr['scale'] * 100)) + ' percent' if fr['scale'] < FRAMING_MIN_SCALE else 'moved'}"
                               f" of its original size in the frame. " + verdict)
                    note = ("Keep the product at exactly the same size and position in the frame as the original; "
                            "do not pull the camera back or rebuild the scene around it. " + (note or ""))
            ready = all(checks.values())
            img["stage_ready"] = ready
            img["verdict"] = verdict
            img["checks"] = checks
            if not ready:
                img["regen_note"] = note
                failing.append(img["file"])
            log(f"  {img['file']}  {'READY' if ready else 'NOT READY'}  {img['verdict'][:100]}")
    write_plan(run, plan)
    return failing


def cmd_verify(args):
    failing = verify_pairs(Path(args.run), only=args.only, force=args.force)
    log(f"{len(failing)} not stage ready: {', '.join(failing) if failing else 'none'}")


# ---------------------------------------------------------------- generate
def prompt_mode(img):
    if img.get("mode") in TEMPLATES:
        return img["mode"]
    return "winter" if img.get("summer") else "scene"


def build_prompt(img):
    scene = img["scene"].strip()
    lock = "Studio packshot. Keep the camera exactly where it is"
    if scene.startswith(lock):   # plans written before 26.9 carried the lock in the scene line
        scene = scene.split("The background stays a bright white studio.", 1)[-1].strip()
    return TEMPLATES[prompt_mode(img)].format(scene=scene)


def fit_box(w, h, size):
    """Where the original sits inside the API canvas: scaled to fit, centred. (x, y, bw, bh, W, H)."""
    W, H = (int(v) for v in size.split("x"))
    s = min(W / w, H / h)
    bw, bh = round(w * s), round(h * s)
    return (W - bw) // 2, (H - bh) // 2, bw, bh, W, H


def _median_edge(strips):
    """Per-channel median of the border strips (no numpy needed)."""
    out = []
    for k in range(3):
        hist = [0] * 256
        for st in strips:
            for i, n in enumerate(st.split()[k].histogram()):
                hist[i] += n
        half, acc = sum(hist) / 2, 0
        for i, n in enumerate(hist):
            acc += n
            if acc >= half:
                out.append(i)
                break
    return tuple(out)


def padded_input(src, size):
    """The original on a canvas of the output's aspect, so the model never has to reframe.
    Without this a 1500x659 packshot sent at 1536x1024 came back with the pan shrunk and moved
    (measured 26.9.2026 on four Lodge frames). Returns (file-like, box) or (None, None) when the
    aspects already match within 3 percent."""
    from PIL import Image
    w, h = image_size(src)
    x, y, bw, bh, W, H = fit_box(w, h, size)
    if abs((w / h) / (W / H) - 1) < 0.03:
        return None, None
    with Image.open(src) as im:
        im = im.convert("RGB")
        # fill with the image's own border colour (white on a packshot, the wall on a photo)
        edges = [im.crop(b) for b in ((0, 0, im.width, 2), (0, im.height - 2, im.width, im.height),
                                      (0, 0, 2, im.height), (im.width - 2, 0, im.width, im.height))]
        fill = _median_edge(edges)
        canvas = Image.new("RGB", (W, H), fill)
        canvas.paste(im.resize((bw, bh), Image.LANCZOS), (x, y))
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    buf.seek(0)
    buf.name = src.stem + ".png"
    return buf, (x, y, bw, bh)


def edit_one(client, src, dest, prompt, quality, run):
    w, h = image_size(src)
    size = api_size_for(w, h)
    t0 = time.time()
    try:
        pad, box = padded_input(src, size)
        if pad is not None:
            r = client.images.edit(model="gpt-image-2", image=[pad], prompt=prompt,
                                   size=size, quality=quality, n=1)
        else:
            with open(src, "rb") as fh:
                r = client.images.edit(model="gpt-image-2", image=[fh], prompt=prompt,
                                       size=size, quality=quality, n=1)
        d = r.data[0]
        if getattr(d, "b64_json", None):
            raw = base64.b64decode(d.b64_json)
        elif getattr(d, "url", None):
            import urllib.request
            raw = urllib.request.urlopen(d.url).read()
        else:
            log(f"  {src.name}: no image data returned")
            return False
        if box is not None:
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as out:
                x, y, bw, bh = box
                out.crop((x, y, x + bw, y + bh)).save(dest, "PNG")
        else:
            dest.write_bytes(raw)
        usage = getattr(r, "usage", None)
        entry = {"file": src.name, "op": "edit", "size": size, "quality": quality,
                 "padded": box is not None, "seconds": round(time.time() - t0, 1)}
        if usage:
            det = getattr(usage, "input_tokens_details", None)
            entry["input_tokens"] = getattr(usage, "input_tokens", 0)
            entry["output_tokens"] = getattr(usage, "output_tokens", 0)
            entry["text_tokens"] = getattr(det, "text_tokens", 0) if det else 0
            entry["image_tokens"] = getattr(det, "image_tokens", 0) if det else 0
            entry["usd_est"] = round(entry["text_tokens"] * RATE_TEXT_IN + entry["image_tokens"] * RATE_IMAGE_IN
                                     + entry["output_tokens"] * RATE_IMAGE_OUT, 4)
        else:
            entry["usd_est"] = FLAT_PER_IMAGE.get(quality, 0.011)
        append_cost(run, entry)
        log(f"  {run.name}/{src.name} -> {dest.name}  ({entry['seconds']}s, est ${entry['usd_est']:.3f})")
        return True
    except Exception as e:
        msg = str(e)
        if "moderation" in msg.lower() or "safety" in msg.lower():
            log(f"  {run.name}/{src.name}: BLOCKED by moderation, skipping (no retry)")
        else:
            log(f"  {run.name}/{src.name}: FAILED {msg[:200]}")
        return False


def pending_jobs(run, only=None):
    """(src, dest, prompt) for every transform in plan.json that has no output yet."""
    plan = read_plan(run)
    outdir = run / "christmas"
    outdir.mkdir(exist_ok=True)
    jobs = []
    for img in plan["images"]:
        if img.get("action") != "transform":
            continue
        if only and img["file"] != only:
            continue
        src = run / "originals" / img["file"]
        dest = outdir / (Path(img["file"]).stem + ".png")
        if dest.exists() and dest.stat().st_size > 10000:
            continue
        if not img.get("scene"):
            log(f"  {img['file']}: no scene line in plan.json, skipping")
            continue
        if img.get("role") in ("packshot", "packshot-tight", "variant", "main"):
            log(f"  {img['file']}: {img.get('role')} is not a lifestyle image, skipping")
            continue
        jobs.append((src, dest, build_prompt(img)))
    return jobs


def run_generate(run, only=None):
    client = openai_client()
    quality = os.getenv("QUALITY", DEFAULT_QUALITY)
    workers = int(os.getenv("WORKERS", "4"))
    jobs = pending_jobs(run, only)
    if not jobs:
        log("Nothing to generate.")
        return
    log(f"Generating {len(jobs)} images (gpt-image-2 edit, quality={quality}, workers={workers})")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(edit_one, client, s, d, p, quality, run) for s, d, p in jobs]
        for f in as_completed(futs):
            if f.result():
                done += 1
    log(f"{done}/{len(jobs)} generated into {run / 'christmas'}")


def cmd_generate(args):
    run_generate(Path(args.run), only=args.only)


def prepare_regen(run, name, note=None):
    """Apply the one-retry rule and move the first attempt aside. Returns True if a retry is allowed."""
    plan = read_plan(run)
    hit = None
    for img in plan["images"]:
        if img["file"] == name:
            hit = img
    if not hit:
        log(f"{name} not in plan.json")
        return False
    if hit.get("regenerated"):
        log(f"{name} was already regenerated once. The rule is one retry. Report it instead.")
        return False
    note = note or hit.get("regen_note")
    if note:
        hit["scene"] = hit["scene"].rstrip(". ") + ". " + note.strip()
    hit["regenerated"] = True
    for k in ("verdict", "stage_ready", "checks"):
        hit.pop(k, None)
    write_plan(run, plan)
    dest = run / "christmas" / (Path(name).stem + ".png")
    if dest.exists():
        shutil.move(dest, run / "christmas" / (Path(name).stem + ".attempt1.png"))
    return True


def cmd_regen(args):
    run = Path(args.run)
    if not prepare_regen(run, args.name, args.note):
        sys.exit(1)
    run_generate(run, only=args.name)


# ---------------------------------------------------------------- check
def mean_luma(path):
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        im = im.convert("L")
        im.thumbnail((256, 256))
        return ImageStat.Stat(im).mean[0]


def cmd_check(args):
    """Numbers to back the eye pass: mean brightness before vs after. A drop over 8 percent is a flag."""
    run = Path(args.run)
    plan = read_plan(run)
    flagged = 0
    for img in plan["images"]:
        if img.get("action") != "transform":
            continue
        src = run / "originals" / img["file"]
        dest = run / "christmas" / (Path(img["file"]).stem + ".png")
        if not dest.exists():
            log(f"{img['file']}: no output")
            continue
        a, b = mean_luma(src), mean_luma(dest)
        delta = (b - a) / max(a, 1) * 100
        flag = "  <-- DARKER, check it" if delta < -8 else ""
        if flag:
            flagged += 1
        log(f"{img['file']}: brightness {a:.0f} -> {b:.0f} ({delta:+.0f}%){flag}")
    log(f"{flagged} flagged. The eye pass (product shape, props, text) is still yours.")


# ---------------------------------------------------------------- sheet + report
def thumb_b64(path, max_px=900):
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def cost_split(costs):
    edits = [c for c in costs if c.get("op", "edit") == "edit"]
    vision = [c for c in costs if c.get("op") in ("classify", "verify")]
    return edits, vision


def write_sheet(run):
    plan = read_plan(run)
    costs = read_costs(run)
    edits, vision = cost_split(costs)
    total = sum(c.get("usd_est", 0) for c in costs)
    total_edits = sum(c.get("usd_est", 0) for c in edits)
    title = plan.get("title") or run.name
    asin = plan.get("asin", run.name)
    rows = []
    for img in plan["images"]:
        src = run / "originals" / img["file"]
        dest = run / "christmas" / (Path(img["file"]).stem + ".png")
        b = thumb_b64(src)
        if img.get("action") == "transform" and dest.exists():
            a = thumb_b64(dest)
            after = f'<a href="christmas/{dest.name}" target="_blank"><img src="{a}" alt="after"></a>'
        else:
            after = f'<div class="skip">Skipped<br><small>{html.escape(img.get("reason", ""))}</small></div>'
        verdict = html.escape(img.get("verdict", ""))
        badge = ""
        if img.get("stage_ready") is True:
            badge = '<span class="badge ok">stage ready</span>'
        elif img.get("stage_ready") is False:
            badge = '<span class="badge no">not stage ready</span>'
        rows.append(f"""
<section class="row">
  <header><strong>{html.escape(img['file'])}</strong> <span class="role">{html.escape(img.get('role',''))}</span> {badge}</header>
  <div class="pair">
    <figure><a href="originals/{src.name}" target="_blank"><img src="{b}" alt="before"></a><figcaption>Before</figcaption></figure>
    <figure>{after}<figcaption>After</figcaption></figure>
  </div>
  {'<p class="verdict">' + verdict + '</p>' if verdict else ''}
</section>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Santa Skill: {html.escape(asin)}</title>
<style>
body{{margin:0;background:#faf7f2;color:#222;font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#666;margin:0 0 24px}}
.row{{background:#fff;border:1px solid #e8e2d8;border-radius:12px;padding:16px;margin-bottom:22px}}
.row header{{margin-bottom:10px}} .role{{color:#888;margin-left:8px;font-size:13px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
figure{{margin:0}} figure img{{width:100%;height:auto;border-radius:8px;display:block;background:#fff}}
figcaption{{text-align:center;color:#777;font-size:13px;margin-top:6px}}
.skip{{display:flex;flex-direction:column;gap:6px;align-items:center;justify-content:center;text-align:center;padding:16px;box-sizing:border-box;aspect-ratio:1;border:2px dashed #ddd;border-radius:8px;color:#888}}
.badge{{font-size:12px;padding:2px 8px;border-radius:999px;margin-left:8px}}
.badge.ok{{background:#e3f5e6;color:#1d6b2c}} .badge.no{{background:#fde8e6;color:#a12a20}}
.verdict{{margin:10px 0 0;color:#444;font-size:14px}}
.foot{{color:#666;font-size:13px;margin-top:30px}}
@media(max-width:640px){{.pair{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>{html.escape(title)}</h1>
<p class="sub">ASIN {html.escape(asin)} &middot; Christmas secondary images &middot; before / after</p>
{''.join(rows)}
<p class="foot">Generated with The Santa Skill (gpt-image-2). Estimated cost for this listing: ${total:.3f} (images ${total_edits:.3f}). Main image untouched per Amazon policy.</p>
</div></body></html>"""
    (run / "contact-sheet.html").write_text(page)

    lines = [f"# Santa Skill report: {asin}", "", f"Product: {title}", ""]
    t = [i for i in plan["images"] if i.get("action") == "transform"]
    s = [i for i in plan["images"] if i.get("action") != "transform"]
    lines.append(f"Transformed: {len(t)}. Skipped: {len(s)}. Total images: {len(plan['images'])}.")
    lines.append("")
    lines.append("## Transformed")
    for i in t:
        ready = "stage ready" if i.get("stage_ready") else ("NOT stage ready" if i.get("stage_ready") is False else "unjudged")
        rg = " (regenerated once)" if i.get("regenerated") else ""
        lines.append(f"- {i['file']} ({i.get('role','')}): {ready}{rg}. {i.get('verdict','')}")
    lines.append("")
    lines.append("## Skipped")
    for i in s:
        lines.append(f"- {i['file']} ({i.get('role','')}): {i.get('reason','')}")
    lines.append("")
    lines.append("## Cost")
    if edits:
        lines.append(f"- Image edits: {len(edits)}. Estimated: ${total_edits:.3f} (quality={edits[0].get('quality')}).")
        toks = sum(c.get("output_tokens", 0) for c in edits)
        if toks:
            lines.append(f"- Billed output image tokens: {toks}. Input image tokens: "
                         f"{sum(c.get('image_tokens', 0) for c in edits)}.")
        per = total_edits / len(edits)
        lines.append(f"- Per image: about ${per:.3f}. A listing with 6 secondary images: about ${per * 6:.2f} at this quality.")
    else:
        lines.append("- No image edits recorded.")
    if vision:
        lines.append(f"- Vision passes (classify + verify, {vision[0].get('model')}): {len(vision)} calls, "
                     f"about ${sum(c.get('usd_est', 0) for c in vision):.3f}.")
    lines.append("- Estimate uses published per-token rates. The exact figure is on the OpenAI usage page.")
    lines.append("")
    lines.append("Outputs: christmas/ (PNG, 1024 px class). Contact sheet: contact-sheet.html.")
    (run / "report.md").write_text("\n".join(lines) + "\n")
    log(f"Wrote {run / 'contact-sheet.html'} and {run / 'report.md'} (est ${total:.3f})")


def cmd_sheet(args):
    write_sheet(Path(args.run))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("source"); f.add_argument("--out"); f.add_argument("--title")
    f.set_defaults(fn=cmd_fetch)
    l = sub.add_parser("list"); l.add_argument("run"); l.set_defaults(fn=cmd_list)
    c2 = sub.add_parser("classify"); c2.add_argument("run"); c2.add_argument("--force", action="store_true")
    c2.set_defaults(fn=cmd_classify)
    g = sub.add_parser("generate"); g.add_argument("run"); g.add_argument("--only"); g.set_defaults(fn=cmd_generate)
    r = sub.add_parser("regen"); r.add_argument("run"); r.add_argument("name"); r.add_argument("--note")
    r.set_defaults(fn=cmd_regen)
    c = sub.add_parser("check"); c.add_argument("run"); c.set_defaults(fn=cmd_check)
    v = sub.add_parser("verify"); v.add_argument("run"); v.add_argument("--only"); v.add_argument("--force", action="store_true")
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("sheet"); s.add_argument("run"); s.set_defaults(fn=cmd_sheet)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
