#!/usr/bin/env python3
"""
catalog.py | The Santa Skill, whole-store mode. Give it an Amazon store, get a Christmas store.

  discover <SOURCE> [--out DIR] [--max N]     find the ASINs, write catalog.json
  run      <DIR> [--budget USD] [--dry-run]    fetch, classify, generate, verify, sheets, index. Resumable.
  status   <DIR>                               one line per product
  index    <DIR>                               rebuild index.html + report.md from what is on disk

SOURCE is one of (the first is the one to ask for):
  product links or ASINs       pasted as they come: any Amazon marketplace, tracking junk and all,
                               app share links (amzn.to, a.co), commas or new lines between them
  a .txt of them               the same, one file
  a Seller Central report      .csv / .tsv / .txt with an asin1 or ASIN column (Active Listings Report),
                               --host www.amazon.co.uk for another marketplace
  the seller's product list    https://www.amazon.com/s?me=SELLERID. Works until Amazon bot-checks it
                               (measured: after four fetches in ten minutes)
  a saved copy of that page    the .html from the browser when curl is bot-checked
  Brand Store and /shop/ pages are refused: JavaScript, and often only part of the catalog.

Caps and stops, by design:
  --max        products taken from a discovery (default 50). Everything found is still listed.
  --budget     USD ceiling on ESTIMATED image spend for this run (default 10; discover prints one that
               covers its estimate). The run stops at the
               ceiling, says what it held back, and the same command continues it later.
  3 blocks     three Amazon captchas in a row pause the run. Same command resumes an hour later.
  duplicates   an image already fetched for another ASIN (bundles, variations) is generated ONCE and
               copied into every folder that uses it.
  one attempt  per page, per image, per generation. One regeneration per failing image, then it is reported.

Layout of the run folder:
  catalog.json                 the state. Every step updates it.
  index.html                   the whole store, before/after. report.md beside it.
  <ASIN>/originals/ christmas/ plan.json contact-sheet.html report.md cost.jsonl
"""
import argparse
import math
import csv
import html
import json
import os
import random
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import santa  # noqa: E402
from santa import log  # noqa: E402

DEFAULT_MAX = 50
DEFAULT_BUDGET = 10.0
WORKERS_DEFAULT = 4
SECONDS_PER_FETCH = 4      # page + gallery + polite pause


# ---------------------------------------------------------------- state
def read_catalog(run):
    p = run / "catalog.json"
    if not p.exists():
        sys.exit(f"catalog.json missing in {run}. Run `discover` first.")
    return json.loads(p.read_text())


def write_catalog(run, cat):
    cat["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (run / "catalog.json").write_text(json.dumps(cat, indent=2, ensure_ascii=False))


def product_dir(run, asin):
    return run / asin


# ---------------------------------------------------------------- discover
def parse_storefront(page):
    """ASINs in gallery order from a search-results page, organic results only."""
    asins = []
    for m in re.finditer(r'<div[^>]+data-component-type="s-search-result"[^>]*data-asin="([A-Z0-9]{10})"', page):
        a = m.group(1)
        if a not in asins:
            asins.append(a)
    if not asins:  # attribute order differs on some layouts
        for m in re.finditer(r'data-asin="([A-Z0-9]{10})"[^>]*data-component-type="s-search-result"', page):
            a = m.group(1)
            if a not in asins:
                asins.append(a)
    titles = {}
    for a in asins:
        m = re.search(r'data-asin="%s".*?<h2[^>]*>.*?<span[^>]*>(.*?)</span>' % a, page, re.S)
        if m:
            titles[a] = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    m = re.search(r"([\d,]+)\s+results", page)
    total = int(m.group(1).replace(",", "")) if m else None
    has_next = bool(re.search(r's-pagination-next(?![^>]*s-pagination-disabled)', page))
    return asins, titles, total, has_next


def discover_storefront(url, cap):
    """Walk the storefront pages. One attempt per page, polite pause, stop on a block."""
    m = re.search(r"[?&]me=([A-Z0-9]+)", url)
    seller = m.group(1) if m else None
    base = url.split("#")[0]
    base = re.sub(r"[?&]page=\d+", "", base)
    found, titles, total = [], {}, None
    page_no = 1
    blocked_note = ""
    while True:
        page_url = base + ("&" if "?" in base else "?") + f"page={page_no}"
        log(f"Storefront page {page_no}: {page_url}")
        pg = santa.curl(page_url)
        if santa.blocked(pg):
            blocked_note = f"Amazon blocked page {page_no}. Products found so far are kept."
            log("  " + blocked_note)
            break
        asins, t, tot, has_next = parse_storefront(pg)
        if tot and total is None:
            total = tot
        new = [a for a in asins if a not in found]
        found += new
        titles.update(t)
        log(f"  {len(asins)} on the page, {len(new)} new, {len(found)} so far" + (f" of {total}" if total else ""))
        if not new or not has_next or len(found) >= cap and cap > 0:
            break
        if page_no >= 40:  # 40 pages is 1,900+ products; nobody dresses that for Christmas in one go
            break
        page_no += 1
        time.sleep(random.uniform(2.0, 4.0))
    return seller, found, titles, total, blocked_note


def read_asin_file(path, host="www.amazon.com"):
    """A .txt of ASINs/links, or a Seller Central report (tab or comma separated, asin1 / ASIN column).
    Returns ((refs, bad), titles) where refs is [(asin, host)]."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    first = text.splitlines()[0] if text.strip() else ""
    asins, titles = [], {}
    if "\t" in first or ("," in first and "asin" in first.lower()):
        dialect = "excel-tab" if "\t" in first else "excel"
        rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
        cols = {c.lower().strip(): c for c in (rows[0].keys() if rows else [])}
        col = cols.get("asin1") or cols.get("asin") or cols.get("(child) asin") or cols.get("child asin")
        name = cols.get("item-name") or cols.get("item name") or cols.get("title") or cols.get("product name")
        if not col:
            sys.exit(f"No asin1 / ASIN column in {path}. Columns: {list(cols.values())[:12]}")
        for r in rows:
            a = santa.asin_from((r.get(col) or "").strip())
            if a and a not in asins:
                asins.append(a)
                if name and r.get(name):
                    titles[a] = r[name].strip()
        return ([(a, host) for a in asins], []), titles
    text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    return santa.refs_from_text(text), titles


STORE_HELP = (
    "Give me the products instead: paste the product links or ASINs (your gift-worthy best sellers;\n"
    "20 is a good start), or put them one per line in a .txt. For the whole catalog, Seller Central >\n"
    "Reports > Inventory Reports > Active Listings Report, and give me that file.")


def cmd_discover(args):
    cap = args.max if args.max is not None else DEFAULT_MAX
    src = args.source
    seller, titles, total, note = None, {}, None, ""
    bad = []
    one = src[0] if len(src) == 1 else ""
    f = Path(os.path.expanduser(one)) if one else None
    if f is not None and not f.exists() and one.lower().endswith((".txt", ".csv", ".tsv", ".html", ".htm")):
        sys.exit(f"File not found: {one}")
    if f is not None and f.is_file() and one.lower().endswith((".html", ".htm")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        asins, titles, total, _ = parse_storefront(text)
        refs = [(a, "www.amazon.com") for a in asins]
        m = re.search(r"[?&]me=([A-Z0-9]+)", text)
        seller = m.group(1) if m else None
        source, name = str(f), seller or re.sub(r"[^A-Za-z0-9_-]+", "-", f.stem)[:40]
    elif f is not None and f.is_file():
        (refs, bad), titles = read_asin_file(f, host=args.host)
        source, name = str(f), re.sub(r"[^A-Za-z0-9_-]+", "-", f.stem)[:40] or "list"
    elif one and "amazon." in one and re.search(r"/stores/|/shop/", one):
        log("That is a Brand Store or an influencer page. It is built by JavaScript, it often shows only")
        log("some of the products, and it cannot be read here.")
        log(STORE_HELP)
        sys.exit(2)
    elif one and "amazon." in one and re.search(r"[?&]me=|/s\?", one):
        # the seller's product list (s?me=SELLERID). Works when Amazon does not bot-check it.
        seller, asins, titles, total, note = discover_storefront(one, cap)
        refs = [(a, "www." + (re.search(r"(amazon\.[a-z.]+)", one).group(1))) for a in asins]
        source, name = one, seller or "storefront"
    else:
        refs, bad = santa.refs_from_text(" ".join(src))
        source = "pasted list"
        name = refs[0][0] if len(refs) == 1 else "my-store"
    for item, why in bad:
        log(f"  skipped {item[:80]}: {why}")
    asins = [a for a, _ in refs]
    hosts = dict(refs)
    if not asins:
        log("No products found. " + note)
        if note or source.startswith("http"):
            log("Amazon serves its search pages behind a bot check that plain curl does not always pass.")
        log(STORE_HELP)
        sys.exit(2)
    total_found = total or len(asins)
    taken = asins[:cap] if cap > 0 else asins
    run = Path(args.out) if args.out else Path.cwd() / "santa-catalog" / name
    run.mkdir(parents=True, exist_ok=True)
    cat = {
        "source": source, "seller_id": seller, "discovered_at": datetime.now().isoformat(timespec="seconds"),
        "total_found": total_found, "all_asins": asins, "cap": cap, "note": note,
        "products": [{"asin": a, "host": hosts.get(a, "www.amazon.com"), "title": titles.get(a, ""),
                      "status": "pending"} for a in taken],
    }
    write_catalog(run, cat)
    log("")
    log(f"Found {total_found} products" + (f" for seller {seller}" if seller else "") + f"; taking {len(taken)}.")
    if len(asins) > len(taken):
        log(f"Taking the first {cap} in the order given. Christmas images pay back on the gift-worthy sellers,")
        log(f"so put those first. To take all {len(asins)}: rerun discover with --max {len(asins)}.")
    if total and total > len(asins):
        log(f"Amazon reports {total} results; {len(asins)} were read before the cap or a block.")
    est_images = len(taken) * 4
    q = santa.DEFAULT_QUALITY
    est = est_images * santa.EST_PER_IMAGE[q]
    mins = (est_images * santa.SECONDS_PER_IMAGE[q] / WORKERS_DEFAULT + len(taken) * SECONDS_PER_FETCH) / 60
    log(f"Rough estimate: ~{est_images} images, ~${est:.2f}, ~{mins:.0f} min (quality {q}, ready to upload).")
    budget = max(int(DEFAULT_BUDGET), math.ceil(est * 1.3))
    log(f"Run folder: {run}")
    log(f"Next: python3 {Path(__file__).name} run {run} --budget {budget}")


# ---------------------------------------------------------------- run phases
def phase_fetch(run, cat):
    """Sequential, polite. Returns False if the run must pause (3 blocks in a row)."""
    todo = [p for p in cat["products"] if p["status"] == "pending"]
    if not todo:
        return True
    log(f"\n== Fetch: {len(todo)} listings")
    streak = 0
    for i, p in enumerate(todo):
        d = product_dir(run, p["asin"])
        if (d / "listing.json").exists() and len(santa.originals_of(d)) >= 2:
            p["status"] = "fetched"
            write_catalog(run, cat)
            continue
        status, n = santa.fetch_listing(p["asin"], d, keep_page=False, host=p.get("host", "www.amazon.com"))
        if status == "blocked":
            streak += 1
            p["status"] = "blocked"
            log(f"  {p['asin']}: BLOCKED ({streak} in a row)")
            write_catalog(run, cat)
            if streak >= 3:
                log("\nThree blocks in a row. Amazon is rate-limiting this address. The run is paused.")
                log("Wait an hour and run the same command; blocked listings are retried once, everything")
                log("already fetched is kept. Or save each blocked listing's images to <ASIN>/originals/ by hand.")
                return False
        else:
            streak = 0
            lst = json.loads((d / "listing.json").read_text())
            p["title"] = p.get("title") or lst.get("title", "")
            p["status"] = "fetched" if status == "ok" else "few"
            if status == "few":
                log(f"  {p['asin']}: fewer than 2 images, nothing to dress")
            write_catalog(run, cat)
        if i < len(todo) - 1:
            time.sleep(random.uniform(1.5, 3.5))
    return True


def phase_dedupe(run, cat):
    """The same image URL in two listings (bundles, variations) is generated once."""
    seen = {}
    for p in cat["products"]:
        if p["status"] not in ("fetched", "classified", "generated", "verified", "done"):
            continue
        d = product_dir(run, p["asin"])
        lst = json.loads((d / "listing.json").read_text())
        dups = {}
        for i, u in enumerate(lst.get("image_urls", []), 1):
            fname = f"{i:02d}" + (".png" if u.lower().endswith(".png") else ".jpg")
            key = u.rsplit("/", 1)[-1].split(".")[0]  # the image id, independent of size suffix
            if key in seen and seen[key][0] != p["asin"]:
                dups[fname] = seen[key]
            else:
                seen.setdefault(key, (p["asin"], fname))
        if dups:
            p["duplicates"] = {k: f"{v[0]}/{v[1]}" for k, v in dups.items()}
    write_catalog(run, cat)


def phase_classify(run, cat):
    todo = [p for p in cat["products"] if p["status"] == "fetched"]
    if not todo:
        return
    log(f"\n== Classify: {len(todo)} listings ({santa.VISION_MODEL})")
    for p in todo:
        d = product_dir(run, p["asin"])
        plan = santa.ensure_plan(d)
        # duplicates never go to the classifier or the image model
        for img in plan["images"]:
            src = (p.get("duplicates") or {}).get(img["file"])
            if src:
                img.update({"role": "duplicate", "action": "skip", "reason": f"same image as {src}",
                            "duplicate_of": src})
        santa.write_plan(d, plan)
        ns = argparse.Namespace(run=str(d), force=False)
        santa.cmd_classify(ns)
        plan = santa.read_plan(d)
        p["images"] = len(plan["images"])
        p["transforms"] = sum(1 for i in plan["images"] if i.get("action") == "transform")
        p["status"] = "classified"
        write_catalog(run, cat)


def spent(run, cat):
    total = 0.0
    for p in cat["products"]:
        d = product_dir(run, p["asin"])
        total += sum(c.get("usd_est", 0) for c in santa.read_costs(d) if c.get("op", "edit") == "edit")
    return total


def phase_generate(run, cat, budget, quality, dry_run):
    jobs = []  # (product, src, dest, prompt)
    for p in cat["products"]:
        if p["status"] not in ("classified", "generated", "verified", "done"):
            continue
        d = product_dir(run, p["asin"])
        for src, dest, prompt in santa.pending_jobs(d):
            jobs.append((p, d, src, dest, prompt))
    already = spent(run, cat)
    per = santa.EST_PER_IMAGE.get(quality, 0.025)
    est = len(jobs) * per
    log(f"\n== Generate: {len(jobs)} images at quality {quality}")
    log(f"   estimate ${est:.2f} (about ${per:.3f} each), spent so far ${already:.2f}, budget ${budget:.2f}, "
        f"ETA ~{len(jobs) * santa.SECONDS_PER_IMAGE.get(quality, 41) / int(os.getenv('WORKERS', '4')) / 60:.0f} min")
    if dry_run:
        log("   dry run: stopping before any image is generated.")
        return "dry"
    if not jobs:
        return "none"
    fits = int(max(0, (budget - already)) // per)
    held = 0
    held_products = set()
    if fits < len(jobs):
        held = len(jobs) - fits
        held_products = {p["asin"] for p, d, s_, dst, pr in jobs[fits:]}
        log(f"   budget holds back {held} images. They run on the next `run` with a higher --budget.")
        jobs = jobs[:fits]
    touched = {p["asin"] for p, d, s_, dst, pr in jobs}
    # a product with images still waiting goes back to "classified" so the next run picks it up;
    # a product that got images this run goes to "generated" so verify and finish look at it again
    for p in cat["products"]:
        if p["asin"] in held_products:
            p["status"] = "classified"
        elif p["asin"] in touched or p["status"] == "classified":
            p["status"] = "generated"
    write_catalog(run, cat)
    if not jobs:
        return "budget"
    client = santa.openai_client()
    workers = int(os.getenv("WORKERS", "4"))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(santa.edit_one, client, s, dst, pr, quality, d): p for p, d, s, dst, pr in jobs}
        for f in as_completed(futs):
            if f.result():
                done += 1
    log(f"   {done}/{len(jobs)} generated")
    return "budget" if held else "ok"


def phase_verify(run, cat, budget, quality):
    todo = [p for p in cat["products"] if p["status"] == "generated"]
    if not todo:
        return
    log(f"\n== Verify: {len(todo)} listings ({santa.VISION_MODEL})")
    regen = []
    for p in todo:
        d = product_dir(run, p["asin"])
        failing = santa.verify_pairs(d)
        for f in failing:
            if santa.prepare_regen(d, f):
                regen.append((p, d, f))
    if regen:
        per = santa.EST_PER_IMAGE.get(quality, 0.025)
        if spent(run, cat) + len(regen) * per > budget:
            log(f"   {len(regen)} regenerations would pass the budget; reported as not stage ready instead.")
            regen = []
        else:
            log(f"\n== Regenerate once: {len(regen)} images")
            client = santa.openai_client("edit")
            with ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "4"))) as ex:
                futs = []
                for p, d, f in regen:
                    for src, dest, prompt in santa.pending_jobs(d, only=f):
                        futs.append(ex.submit(santa.edit_one, client, src, dest, prompt, quality, d))
                for fu in as_completed(futs):
                    fu.result()
            for p, d, f in regen:
                santa.verify_pairs(d, only=f)
    unjudged = 0
    for p in todo:
        d = product_dir(run, p["asin"])
        plan = santa.read_plan(d)
        p["ready"] = sum(1 for i in plan["images"] if i.get("stage_ready") is True)
        p["not_ready"] = sum(1 for i in plan["images"] if i.get("stage_ready") is False)
        waiting = [i for i in plan["images"] if i.get("action") == "transform" and "stage_ready" not in i
                   and (d / "christmas" / (Path(i["file"]).stem + ".png")).exists()]
        if waiting:
            unjudged += len(waiting)   # stays "generated": the same command verifies these again
        else:
            p["status"] = "verified"
    if unjudged:
        log(f"   {unjudged} images got no answer from the verifier (timeout). Run the same command again.")
    write_catalog(run, cat)


def phase_finish(run, cat):
    """Copy duplicate outputs into every folder that uses them, write per-ASIN sheets, mark done."""
    for p in cat["products"]:
        if p["status"] != "verified":
            continue
        d = product_dir(run, p["asin"])
        plan = santa.read_plan(d)
        for img in plan["images"]:
            src = img.get("duplicate_of")
            if not src:
                continue
            s_asin, s_file = src.split("/")
            s_plan = santa.read_plan(product_dir(run, s_asin))
            s_img = next((i for i in s_plan["images"] if i["file"] == s_file), None)
            s_out = product_dir(run, s_asin) / "christmas" / (Path(s_file).stem + ".png")
            if s_img and s_img.get("action") == "transform" and s_out.exists():
                (d / "christmas").mkdir(exist_ok=True)
                shutil.copy(s_out, d / "christmas" / (Path(img["file"]).stem + ".png"))
                img.update({"action": "transform", "scene": s_img.get("scene", ""),
                            "stage_ready": s_img.get("stage_ready"), "verdict": f"copied from {src}. " + s_img.get("verdict", ""),
                            "reason": ""})
            elif s_img:
                img["reason"] = f"same image as {src}: " + (s_img.get("reason") or "not generated")
        santa.write_plan(d, plan)
        santa.write_sheet(d)
        p["cost"] = round(sum(c.get("usd_est", 0) for c in santa.read_costs(d)), 4)
        p["ready"] = sum(1 for i in plan["images"] if i.get("stage_ready") is True)
        p["transforms"] = sum(1 for i in plan["images"] if i.get("action") == "transform")
        p["generated"] = sum(1 for i in plan["images"] if i.get("action") == "transform"
                             and (d / "christmas" / (Path(i["file"]).stem + ".png")).exists())
        p["status"] = "done"
    write_catalog(run, cat)


def cmd_run(args):
    run = Path(args.run)
    cat = read_catalog(run)
    budget = args.budget if args.budget is not None else DEFAULT_BUDGET
    quality = args.quality or os.getenv("QUALITY", santa.DEFAULT_QUALITY)
    os.environ["QUALITY"] = quality
    t0 = time.time()
    log(f"Santa catalog run: {len(cat['products'])} products, quality {quality}, budget ${budget:.2f}")
    # a paused run retries its blocked listings once
    for p in cat["products"]:
        if p["status"] == "blocked" and not p.get("retried"):
            p["status"], p["retried"] = "pending", True
    if not phase_fetch(run, cat):
        write_index(run, cat)
        return
    phase_dedupe(run, cat)
    phase_classify(run, cat)
    outcome = phase_generate(run, cat, budget, quality, args.dry_run)
    if outcome == "dry":
        write_index(run, cat)
        return
    phase_verify(run, cat, budget, quality)
    phase_finish(run, cat)
    write_index(run, cat)
    mins = (time.time() - t0) / 60
    done = [p for p in cat["products"] if p["status"] == "done"]
    n_t = sum(p.get("generated", 0) for p in done)
    n_r = sum(p.get("ready", 0) for p in done)
    waiting = sum(p.get("transforms", 0) for p in cat["products"] if p["status"] == "classified")
    log(f"\nDone: {len(done)}/{len(cat['products'])} products, {n_t} Christmas images ({n_r} stage ready), "
        f"est ${spent(run, cat):.2f}, {mins:.1f} min.")
    if outcome == "budget":
        log(f"Budget reached with {waiting} images still waiting. Run the same command with a higher --budget to finish.")
    log(f"Open: {run / 'index.html'}")


# ---------------------------------------------------------------- status / index
def cmd_status(args):
    run = Path(args.run)
    cat = read_catalog(run)
    log(f"{cat.get('source')}  ({len(cat['products'])} of {cat.get('total_found')} products)")
    for p in cat["products"]:
        log(f"  {p['asin']}  {p['status']:<10} {p.get('transforms', '-')!s:>3} planned "
            f"{p.get('generated', '-')!s:>3} made {p.get('ready', '-')!s:>3} ready  ${p.get('cost', 0):.2f}  {p.get('title', '')[:60]}")
    log(f"spent (images): ${spent(run, cat):.2f}")


def write_index(run, cat):
    cards = []
    n_products = n_images = n_ready = 0
    total_cost = 0.0
    image_cost = spent(run, cat)
    for p in cat["products"]:
        d = product_dir(run, p["asin"])
        title = html.escape(p.get("title") or p["asin"])
        if p["status"] != "done":
            cards.append(f'<section class="card pending"><h2>{title}</h2><p class="meta">{p["asin"]} &middot; {p["status"]}</p></section>')
            continue
        plan = santa.read_plan(d)
        n_products += 1
        total_cost += p.get("cost", 0)
        tiles = []
        for img in plan["images"]:
            out = d / "christmas" / (Path(img["file"]).stem + ".png")
            if img.get("action") != "transform" or not out.exists():
                continue
            n_images += 1
            if img.get("stage_ready"):
                n_ready += 1
            a = santa.thumb_b64(out, 520)
            b = santa.thumb_b64(d / "originals" / img["file"], 520)
            flag = "" if img.get("stage_ready") is not False else '<span class="flag">check</span>'
            tiles.append(f'<figure class="tile"><img class="after" src="{a}" alt=""><img class="before" src="{b}" alt="">{flag}</figure>')
        if not tiles:
            why = "; ".join(f"{i['file']}: {i.get('reason') or i.get('role')}" for i in plan["images"][1:]) or "no secondary images"
            tiles.append(f'<p class="meta">Nothing to dress here. {html.escape(why)}</p>')
        main = d / "originals" / plan["images"][0]["file"] if plan["images"] else None
        main_b64 = santa.thumb_b64(main, 400) if main and main.exists() else ""
        cards.append(f"""<section class="card">
  <div class="head"><img class="main" src="{main_b64}" alt=""><div><h2>{title}</h2>
  <p class="meta">{p['asin']} &middot; {len(tiles)} Christmas images &middot; <a href="{p['asin']}/contact-sheet.html">before / after sheet</a></p></div></div>
  <div class="tiles">{''.join(tiles)}</div>
</section>""")
    src = html.escape(str(cat.get("source", "")))
    who = cat.get("seller_id") or run.name
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Christmas store: {html.escape(who)}</title>
<style>
:root{{--bg:#0f1a14;--card:#16241c;--ink:#f4efe6;--mute:#a7b3aa;--gold:#e0b45a;--red:#c8433a}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1400px;margin:0 auto;padding:32px 20px 80px}}
h1{{font-size:30px;margin:0 0 6px;letter-spacing:.2px}} h1 span{{color:var(--gold)}}
.sub{{color:var(--mute);margin:0 0 30px}} .sub b{{color:var(--ink)}}
.card{{background:var(--card);border:1px solid #24352b;border-radius:16px;padding:18px;margin-bottom:22px}}
.card.pending{{opacity:.55}}
.head{{display:flex;gap:16px;align-items:center;margin-bottom:14px}}
.head .main{{width:84px;height:84px;object-fit:contain;background:#fff;border-radius:10px;flex:none}}
h2{{font-size:17px;margin:0 0 2px;font-weight:600}} .meta{{color:var(--mute);margin:0;font-size:13px}} .meta a{{color:var(--gold)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}}
.tile{{margin:0;position:relative;aspect-ratio:1;border-radius:12px;overflow:hidden;background:#fff}}
.tile img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:opacity .25s}}
.tile .before{{opacity:0}} .tile:hover .before{{opacity:1}}
.tile::after{{content:"hover: before";position:absolute;left:8px;bottom:8px;font-size:11px;color:#fff;background:rgba(0,0,0,.45);padding:2px 7px;border-radius:999px;opacity:0;transition:opacity .2s}}
.tile:hover::after{{opacity:1;content:"before"}}
.flag{{position:absolute;top:8px;right:8px;font-size:11px;background:var(--red);color:#fff;padding:2px 8px;border-radius:999px}}
.foot{{color:var(--mute);font-size:13px;margin-top:30px}}
</style></head><body><div class="wrap">
<h1>Your store, <span>dressed for Christmas</span></h1>
<p class="sub"><b>{n_products}</b> products &middot; <b>{n_images}</b> Christmas images &middot; {n_ready} passed every check &middot; est ${total_cost:.2f} (images ${image_cost:.2f}) &middot; main images untouched &middot; source: {src}</p>
{''.join(cards)}
<p class="foot">Generated with The Santa Skill, catalog mode (gpt-image-2). Hover any image for the original. Each product folder holds originals/, christmas/ and its own before/after sheet.</p>
</div></body></html>"""
    (run / "index.html").write_text(page)

    lines = [f"# Santa catalog report: {who}", "", f"Source: {cat.get('source')}",
             f"Products found: {cat.get('total_found')}. Taken: {len(cat['products'])}. Done: {n_products}.",
             f"Christmas images: {n_images}. Stage ready: {n_ready}. Estimated cost: ${total_cost:.2f} "
             f"(image edits ${image_cost:.2f}, the rest is the two vision passes).", "",
             "| ASIN | status | images | transformed | ready | cost | title |", "|---|---|---|---|---|---|---|"]
    for p in cat["products"]:
        lines.append(f"| {p['asin']} | {p['status']} | {p.get('images', '')} | {p.get('transforms', '')} | "
                     f"{p.get('ready', '')} | ${p.get('cost', 0):.2f} | {(p.get('title') or '')[:70]} |")
    if cat.get("note"):
        lines += ["", cat["note"]]
    lines += ["", "Each <ASIN>/ folder: originals/ (the way back in January), christmas/ (the uploads), "
              "contact-sheet.html (before/after with verdicts), report.md."]
    (run / "report.md").write_text("\n".join(lines) + "\n")
    log(f"Wrote {run / 'index.html'} and {run / 'report.md'}")


def cmd_index(args):
    run = Path(args.run)
    write_index(run, read_catalog(run))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover"); d.add_argument("source", nargs="+"); d.add_argument("--out")
    d.add_argument("--max", type=int, help=f"products to take (default {DEFAULT_MAX}, 0 = all)")
    d.add_argument("--host", default="www.amazon.com", help="marketplace for a Seller Central report (www.amazon.co.uk, ...)")
    d.set_defaults(fn=cmd_discover)
    r = sub.add_parser("run"); r.add_argument("run"); r.add_argument("--budget", type=float)
    r.add_argument("--quality", choices=["low", "medium", "high"]); r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("status"); s.add_argument("run"); s.set_defaults(fn=cmd_status)
    i = sub.add_parser("index"); i.add_argument("run"); i.set_defaults(fn=cmd_index)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
