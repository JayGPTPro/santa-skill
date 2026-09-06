#!/usr/bin/env python3
"""
santa.py | The Santa Skill pipeline. Christmas versions of Amazon secondary images.

Subcommands (all take the run folder as the last argument, default ./santa/<ASIN>):

  fetch  <ASIN|URL|folder> [--out DIR]   download listing images (or copy a folder) to originals/
  list   <DIR>                            print originals with size and aspect (for the agent to classify)
  generate <DIR> [--only NAME]           run gpt-image-2 images.edit on every "transform" entry in plan.json
  regen  <DIR> <NAME> [--note TEXT]      delete one output and generate it again (the one allowed retry)
  check  <DIR>                            mean brightness before vs after per image (flags a drop over 8%)
  sheet  <DIR>                            write contact-sheet.html and report.md from plan.json + cost log

Env:
  OPENAI_API_KEY   required for generate/regen (fallback: ~/Downloads/Claude/.env)
  QUALITY          low (default) | medium | high
  WORKERS          parallel workers, default 4

Rules the prompt is built from: ../references/christmas-prompt-rules.md
No retries against Amazon. No retries against OpenAI. One attempt per image, by design.
"""
import argparse
import base64
import html
import io
import json
import os
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

PROMPT_TEMPLATE = (
    "Create a clean premium Christmas version of this exact image. "
    "Keep the original photo fully intact with no changes to people, faces, clothing, poses, "
    "hands, product shape, materials, colors, lighting, shadows, depth, or camera angle. "
    "Keep every existing headline, label and graphic element exactly as written. "
    "Add only subtle realistic holiday accents that fit naturally. Use warm lights, soft bokeh, "
    "light garland, small greenery, candles, or wrapped gift boxes: at most three added props in total, "
    "placed on an existing surface at the far side from the product, never next to it. "
    "Do not add Christmas trees unless the scene is clearly a home living room. In a living room "
    "you may add one realistic tree placed naturally. In any other location do not add any trees. "
    "All additions must match the real lighting, shadows, and color tones. "
    "Keep everything photorealistic with no cartoon items, fake sparkles, snow overlays, glitter, "
    "or floating objects. No snow on the ground or on objects; snow may appear only as a view through a window. "
    "If adults appear you may add one natural red Santa hat to one adult. The hat must match head "
    "shape, shadow, and lighting. "
    "If the image includes graphic elements you may add soft Christmas graphics or elements. "
    "Nothing may be placed on or touching the product. "
    "The result should look naturally decorated for Christmas. Warm, elegant and subtle.\n"
    "Scene notes for this image: {scene}"
)

# Rough per-token rates used only for the cost ESTIMATE in report.md.
# Published gpt-image-1 rates; gpt-image-2 is billed the same way (tokens in, image tokens out).
# The real number is on the OpenAI usage page. The script prints the token counts it was billed.
RATE_TEXT_IN = 5.0 / 1_000_000
RATE_IMAGE_IN = 10.0 / 1_000_000
RATE_IMAGE_OUT = 40.0 / 1_000_000
# Fallback flat rates per output image when the API returns no usage block.
FLAT_PER_IMAGE = {"low": 0.011, "medium": 0.042, "high": 0.167}


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def asin_from(text):
    m = re.search(r"(?:/dp/|/gp/product/|/product/)([A-Z0-9]{10})", text)
    if m:
        return m.group(1)
    m = re.fullmatch(r"[A-Z0-9]{10}", text.strip())
    return m.group(0) if m else None


def load_key():
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key
    env = Path.home() / "Downloads/Claude/.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


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
        sys.exit(f"plan.json missing in {run}. The agent writes it after looking at originals/.")
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
    return (not page) or ("api-services-support@amazon.com" in page) or \
        ("Type the characters you see" in page) or ("captcha" in page.lower() and "productTitle" not in page)


def cmd_fetch(args):
    src = args.source
    out = Path(args.out) if args.out else None
    src_path = Path(os.path.expanduser(src))
    if src_path.is_dir():
        run = out or Path.cwd() / "santa" / src_path.name
        originals = run / "originals"
        originals.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in sorted(src_path.iterdir()):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                shutil.copy(p, originals / p.name)
                n += 1
        listing = {"asin": src_path.name, "title": args.title or src_path.name,
                   "source": "folder", "folder": str(src_path), "images": n}
        (run / "listing.json").write_text(json.dumps(listing, indent=2))
        log(f"Copied {n} images from {src_path} to {originals}")
        if n < 2:
            sys.exit("Fewer than 2 images. Nothing to transform beyond a main image.")
        return

    asin = asin_from(src)
    if not asin:
        sys.exit(f"Not an ASIN, Amazon URL or folder: {src}")
    run = out or Path.cwd() / "santa" / asin
    originals = run / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    url = f"https://www.amazon.com/dp/{asin}"
    log(f"Fetching {url} (one attempt, no retries)")
    page = curl(url)
    (run / "page.html").write_text(page)
    if blocked(page):
        log("")
        log("AMAZON FETCH BLOCKED OR EMPTY.")
        log("Amazon returned a captcha or nothing. This skill will not retry (retries get IPs blocked).")
        log("Fallback: save the listing images to a folder and re-run with that folder:")
        log(f"  python3 {HERE / 'santa.py'} fetch /path/to/images --out {run}")
        sys.exit(2)
    title, urls = parse_listing(page)
    listing = {"asin": asin, "title": title, "source": url, "image_urls": urls}
    (run / "listing.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False))
    log(f"Title: {title[:90]}")
    log(f"Found {len(urls)} image URLs")
    if len(urls) < 2:
        log("Fewer than 2 gallery images found. Use the folder fallback:")
        log(f"  python3 {HERE / 'santa.py'} fetch /path/to/images --out {run}")
        sys.exit(2)
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
    if ok < 2:
        sys.exit(2)


# ---------------------------------------------------------------- list
def cmd_list(args):
    run = Path(args.run)
    originals = run / "originals"
    files = sorted(p for p in originals.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    rows = []
    for p in files:
        w, h = image_size(p)
        rows.append({"file": p.name, "path": str(p), "width": w, "height": h, "api_size": api_size_for(w, h)})
        log(f"{p.name}  {w}x{h}  -> {api_size_for(w, h)}")
    if not (run / "plan.json").exists():
        listing = {}
        lp = run / "listing.json"
        if lp.exists():
            listing = json.loads(lp.read_text())
        plan = {"asin": listing.get("asin", run.name), "title": listing.get("title", ""),
                "images": [{"file": r["file"], "role": "TODO", "action": "TODO", "scene": "", "reason": ""}
                           for r in rows]}
        write_plan(run, plan)
        log(f"\nWrote plan.json template. Fill role (main|lifestyle|packshot|infographic|logo), "
            f"action (transform|skip), scene, reason.")


# ---------------------------------------------------------------- generate
def build_prompt(scene):
    return PROMPT_TEMPLATE.format(scene=scene.strip())


def edit_one(client, src, dest, prompt, quality, run):
    from openai import OpenAI  # noqa
    w, h = image_size(src)
    size = api_size_for(w, h)
    t0 = time.time()
    try:
        with open(src, "rb") as fh:
            r = client.images.edit(model="gpt-image-2", image=[fh], prompt=prompt,
                                   size=size, quality=quality, n=1)
        d = r.data[0]
        if getattr(d, "b64_json", None):
            dest.write_bytes(base64.b64decode(d.b64_json))
        elif getattr(d, "url", None):
            import urllib.request
            urllib.request.urlretrieve(d.url, dest)
        else:
            log(f"  {src.name}: no image data returned")
            return False
        usage = getattr(r, "usage", None)
        entry = {"file": src.name, "size": size, "quality": quality, "seconds": round(time.time() - t0, 1)}
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
        log(f"  {src.name} -> {dest.name}  ({entry['seconds']}s, est ${entry['usd_est']:.3f})")
        return True
    except Exception as e:
        msg = str(e)
        if "moderation" in msg.lower() or "safety" in msg.lower():
            log(f"  {src.name}: BLOCKED by moderation, skipping (no retry)")
        else:
            log(f"  {src.name}: FAILED {msg[:200]}")
        return False


def run_generate(run, only=None):
    key = load_key()
    if not key:
        sys.exit("OPENAI_API_KEY missing. export OPENAI_API_KEY=sk-... or put it in ~/Downloads/Claude/.env")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("openai package missing. Fix: python3 -m pip install openai")
    client = OpenAI(api_key=key)
    quality = os.getenv("QUALITY", "low")
    workers = int(os.getenv("WORKERS", "4"))
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
            log(f"  {dest.name} exists, skipping")
            continue
        if not img.get("scene"):
            log(f"  {img['file']}: no scene line in plan.json, skipping")
            continue
        jobs.append((src, dest, build_prompt(img["scene"])))
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
    log(f"{done}/{len(jobs)} generated into {outdir}")


def cmd_generate(args):
    run_generate(Path(args.run), only=args.only)


def cmd_regen(args):
    run = Path(args.run)
    plan = read_plan(run)
    hit = None
    for img in plan["images"]:
        if img["file"] == args.name:
            hit = img
    if not hit:
        sys.exit(f"{args.name} not in plan.json")
    if hit.get("regenerated"):
        sys.exit(f"{args.name} was already regenerated once. The rule is one retry. Report it instead.")
    if args.note:
        hit["scene"] = hit["scene"].rstrip(". ") + ". " + args.note.strip()
    hit["regenerated"] = True
    write_plan(run, plan)
    dest = run / "christmas" / (Path(args.name).stem + ".png")
    if dest.exists():
        shutil.move(dest, run / "christmas" / (Path(args.name).stem + ".attempt1.png"))
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


def cmd_sheet(args):
    run = Path(args.run)
    plan = read_plan(run)
    costs = read_costs(run)
    total = sum(c.get("usd_est", 0) for c in costs)
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
<p class="foot">Generated with The Santa Skill (gpt-image-2). Estimated generation cost for this run: ${total:.3f}. Main image untouched per Amazon policy.</p>
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
    if costs:
        lines.append(f"- API calls: {len(costs)}. Estimated total: ${total:.3f} "
                     f"(quality={costs[0].get('quality')}).")
        toks = sum(c.get("output_tokens", 0) for c in costs)
        if toks:
            lines.append(f"- Billed output image tokens: {toks}. Input image tokens: "
                         f"{sum(c.get('image_tokens', 0) for c in costs)}.")
        per = total / len(costs)
        lines.append(f"- Per image: about ${per:.3f}. A listing with 6 secondary images: about ${per * 6:.2f} at this quality.")
        lines.append("- Estimate uses published per-token image rates. The exact figure is on the OpenAI usage page.")
    else:
        lines.append("- No API calls recorded.")
    lines.append("")
    lines.append("Outputs: christmas/ (PNG, 1024 px class). Contact sheet: contact-sheet.html.")
    (run / "report.md").write_text("\n".join(lines) + "\n")
    log(f"Wrote {run / 'contact-sheet.html'} and {run / 'report.md'} (est ${total:.3f})")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("source"); f.add_argument("--out"); f.add_argument("--title")
    f.set_defaults(fn=cmd_fetch)
    l = sub.add_parser("list"); l.add_argument("run"); l.set_defaults(fn=cmd_list)
    g = sub.add_parser("generate"); g.add_argument("run"); g.add_argument("--only"); g.set_defaults(fn=cmd_generate)
    r = sub.add_parser("regen"); r.add_argument("run"); r.add_argument("name"); r.add_argument("--note")
    r.set_defaults(fn=cmd_regen)
    c = sub.add_parser("check"); c.add_argument("run"); c.set_defaults(fn=cmd_check)
    s = sub.add_parser("sheet"); s.add_argument("run"); s.set_defaults(fn=cmd_sheet)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
