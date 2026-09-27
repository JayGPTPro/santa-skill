---
name: santa-skill
description: >-
  The Santa Skill. Turn Amazon listings' secondary images into Christmas versions before
  Q4, with the product untouched and the main image left alone. One command in: one product
  link or ASIN, a folder of product images, or MANY products at once (pasted product links
  or ASINs from any Amazon marketplace, app share links, a .txt of them, or a Seller Central
  Active Listings Report). Out: a christmas/ folder per product, a before/after sheet each,
  and one page showing them all. Zero questions during the run. Use when the user says
  "santa skill", "christmas images", "christmas mode", "Q4 images", "holiday listing
  images", "seasonal images", "xmas listing", "christmas my store", "my whole catalog",
  "all my products", or pastes Amazon product links or ASINs together with christmas or
  holiday: "/santa-skill B0GYY18GYC", "/santa-skill <20 product links>", "make my store
  christmas", "holiday versions of my listing images".
---

# The Santa Skill

One line in: `/santa-skill B0GYY18GYC`. Out: Christmas versions of the listing's
secondary images in `santa/<ASIN>/christmas/`, a before/after `contact-sheet.html`, and a
short `report.md`. The main image is never touched (Amazon wants it on pure white). The
product is never touched either: same shape, color, angle, label. The world around it gets a
Christmas you can see at thumbnail size: a lit tree, garlands, gifts; a summer scene turns to
snow and winter coats; a pool or a beach keeps its sun and swimwear and gets a Christmas in the sun.

Many products in: `/santa-skill` with the product links pasted after it. Out: the same
thing for every product, one folder per ASIN, and one `index.html` that shows the whole set
dressed. That is catalog mode, the second half of this file.

This is an AUTOPILOT skill. After the environment check passes, the run asks nothing.
The only stop is a missing input (Amazon blocked the fetch and no folder was given).
Every judgment call is made, logged in plan.json, and shown on the sheet.

Engine: OpenAI `gpt-image-2` through `images.edit`, the original photo as the input
image. Never Nano Banana, never Gemini, never any other image model, not as a fallback.
If gpt-image-2 is unavailable, stop and say so. The two looking passes (classify, verify)
run on `gpt-5.4` vision through the same key.

## Files

```
SKILL.md                              this file
scripts/check-env.sh                  prints OK / MISSING with exact fix lines
scripts/santa.py                      one listing: fetch, list, classify, generate, regen, check, verify, sheet
scripts/catalog.py                    one store: discover, run, status, index
references/christmas-prompt-rules.md  the rules every prompt is built from, and the self-check
```

`S=~/.claude/skills/santa-skill/scripts` in the commands below. Run everything from the
user's working directory; a listing lands in `<cwd>/santa/<ASIN>/`, a store in
`<cwd>/santa-catalog/<name>/` (`my-store` for a paste, the file name for a file).

## Step 0: environment (first run on a machine, or after an error)

```
bash $S/check-env.sh
```

If anything prints MISSING, show the user the fix lines it printed and stop. Needs:
python3, the `openai` package, Pillow, curl, and `OPENAI_API_KEY` (plus, optional,
`opencv-python-headless` for the framing check) (env var, else `./.env`,
else `~/Downloads/Claude/.env`). gpt-image-2 needs a verified OpenAI org.

## Which mode

| The user gives | Mode |
|---|---|
| one ASIN, one product link, one folder of images | single listing, Steps 1 to 5 below |
| two or more product links or ASINs (pasted, or in a .txt), or a Seller Central report | catalog, the section after Step 5 |
| a folder of product folders (one product each, images in gallery order), or several product folders | catalog, from the folders: no Amazon fetch |
| "my store", "my whole catalog", a store link, with no product list | ask for the list, in these words: "Paste the product links or ASINs you want dressed, your gift-worthy best sellers first. 20 is a good start. For the whole catalog, download the Active Listings Report from Seller Central (Reports > Inventory Reports) and give me the file." Then catalog mode |

Why the list and not a store link: "the store" is three different pages on Amazon. The Brand
Store (`/stores/...`) is JavaScript and often shows only part of the catalog; the seller page
(`s?me=...`) holds everything the seller sells, other brands included, and Amazon puts it
behind a bot check (measured: after four fetches in ten minutes); a brand search mixes in
other sellers. Product pages were read cleanly all day. A list is unambiguous, works every
time, and the seller decides the scope, so a 500-product catalog is never a surprise.

---

# Single listing

## Step 1: fetch

```
python3 $S/santa.py fetch B0GYY18GYC
python3 $S/santa.py fetch "https://www.amazon.com/dp/B0GYY18GYC"
python3 $S/santa.py fetch /path/to/folder/of/images --out santa/MYPRODUCT --title "Product name"
```

Plain curl with a desktop User-Agent, one attempt, hi-res URLs parsed from the page's
`"hiRes"` entries (fallback `"large"`). Writes `originals/01.jpg ...` in gallery order
(01 is the main image), `listing.json` with the title, and `page.html` for debugging.

Exit code 2 means blocked or fewer than 2 images. The script prints the folder fallback
line. Relay it to the user word for word and STOP. Do not retry the Amazon fetch. Do not
retry a failed image download. Do not open Amazon in a browser to work around it. When
the user comes back with a folder, run the folder form and continue.

## Step 2: classify

```
python3 $S/santa.py list santa/B0GYY18GYC
python3 $S/santa.py classify santa/B0GYY18GYC
```

`list` prints every original and writes the `plan.json` template. `classify` looks at every
picture with gpt-5.4 vision and fills each entry: `seen` (what is in the picture), `role`
(`main` | `lifestyle` | `packshot` | `infographic` | `logo` | `collage` | `variant`; only `lifestyle` transforms), `action`
(`transform` | `skip`), a `scene` line for every transform, a `reason` for every skip, and
`text_on_image` (the headline and labels it read, so the verify pass can read them back).
About 5 seconds and half a cent per gallery.

Then OPEN `plan.json` and the originals and check the calls. You are the second opinion:
a lifestyle frame marked infographic, or a collage marked lifestyle, gets edited by hand
before generation. `--force` reclassifies everything. The decision rules:

- `01` (first in gallery) is the main image. Skip, always.
- Lifestyle scene, with or without a headline band: transform. A headline plus a sub-line
  is the normal Amazon lifestyle frame; gpt-image-2 keeps short text intact.
- Infographic (five or more text elements, feature bullets, spec tables, dimension arrows,
  diagrams, or text across the product's body): skip. Small text gets mangled.
- The product on a plain white or studio background, in any form: skip, "product on white".
  Santa dresses lifestyle images only: never the main image, never a packshot, never an
  infographic. The code enforces it too: a plan that says transform on a packshot is skipped.
- A color variant (the product in another color on white) is also named as such, so the
  report says why. The classifier sees the whole gallery as a numbered strip
  (`gallery-strip.jpg`) beside each image, so it can tell.
- Brand logo card, brand graphic (logo + claim on a flat color), retail box, multi-panel
  collage, several colors side by side: skip.

A few borderline frames (a lifestyle photo with a lot of text) can land either way between
runs. That costs a skipped frame or a few cents, never a broken image: every transform still
goes through the verify gate.

The Christmas is RICH, on purpose: it has to read as Christmas at Amazon thumbnail size.
Only lifestyle images are transformed. Each gets a `mode` that picks its prompt (full detail
in the rules file):

- `scene`: one large anchor (a lit, decorated tree in any home interior; a big wreath or lit garland
  elsewhere) plus 3 or 4 supporting elements, string lights generously. Clothing unchanged.
- `winter`: a summer scene becomes full winter: snow outdoors, the same people in winter clothing in
  the same poses, then the Christmas additions.
- `sun`: people in the water, at a pool or on a beach, or in swimwear. Never snow or coats: the same
  swimwear and sunshine, plus string lights, tinsel, a small decorated palm or tree, gifts, red and gold.
  It wins over `winter`; the code also turns a winter pick whose `seen` line names a pool, a beach, the
  sea or swimwear into `sun`.
At most one Santa hat, on an adult. The scene line says what goes WHERE and what must stay exactly
("Keep the dog and the gray dog bed exactly as they are, nothing on the bed.").

## Step 3: generate

```
python3 $S/santa.py generate santa/B0GYY18GYC
```

Parallel (4 workers), quality `medium` by default, ONE attempt per image, skips outputs that
already exist. Prompt = the fixed template from the rules file with the scene line dropped
in. Output size follows the original's aspect: square 1024x1024, landscape 1536x1024,
portrait 1024x1536. When the original's aspect differs from that by more than 3 percent (a
1500x659 skillet banner, say), the original is placed on a canvas of the output's aspect,
filled with its own border colour, and the output is cropped back to the original's frame.
Without that the model reframes and the product shrinks (measured: four Lodge frames came
back at 62 to 80 percent of their size). Each call appends its token usage and a cost estimate to `cost.jsonl`.
Measured: 14 to 17 seconds per image, four images in 17 seconds wall clock.

A moderation block or an API error is logged and skipped. No retry loop, ever: the SDK's own
retries are off for edits, and every call has a timeout (edit 180 s, vision 60 s, one retry
for vision), because four hung calls once held a run for five minutes. Disney-licensed
products (Mickey, princess plates) are refused by OpenAI moderation; the report names them.

## Step 4: verify

```
python3 $S/santa.py check santa/B0GYY18GYC
python3 $S/santa.py verify santa/B0GYY18GYC
```

`check` prints mean brightness before vs after and flags a drop over 8 percent; dark props
on a bright edge trip it, so it is a pointer, not a verdict. `verify` puts each pair in front
of gpt-5.4 with the nine questions from the rules file (same product, nothing added on it, not
darker, no tint, nothing floating, people unchanged, text intact, nothing banned, obviously
Christmas at thumbnail size), then MEASURES a tenth: `same_framing`. It finds the original's product inside the output at 55 to
120 percent scale (OpenCV template match) and fails the frame below 0.88 or when the product
moved more than 12 percent of the width. The model cannot judge size: it passed two of three
frames whose product had shrunk by a quarter; the measurement got twelve of twelve. Without
OpenCV the ninth check is skipped (check-env says so). It writes `stage_ready`, `verdict`,
`checks`, `framing` and, on a fail, a `regen_note` into plan.json; a framing fail puts
"keep the product at exactly the same size and position" into that note, and in the test
both shrunk frames came back at full size on their one retry.

Then OPEN every `christmas/*.png` beside its original yourself. Read the small text on the
product's own label at native size: a digit that changed is a note in the verdict, and the
vision pass can miss it. Your eye overrides the gate in both directions; edit plan.json.

One failing image gets ONE regeneration, the failure written into the scene (`--note`
overrides the stored `regen_note`):

```
python3 $S/santa.py regen santa/B0GYY18GYC 06.jpg --note "The tent must stay orange. No ribbon on the product."
python3 $S/santa.py verify santa/B0GYY18GYC --only 06.jpg
```

`regen` refuses a second retry by design. After it, the verdict says not stage ready and
why, and the run moves on.

## Step 5: contact sheet + report

```
python3 $S/santa.py sheet santa/B0GYY18GYC
```

Writes `contact-sheet.html` (self-contained, thumbnails embedded, product title on top,
one before/after row per image, skipped images shown with their reason, the verdict under
each row) and `report.md` (transformed, skipped and why, cost per image and per listing).
Open the sheet for the user: `open santa/B0GYY18GYC/contact-sheet.html`.

## What you tell the user at the end (single listing)

Four lines, then the paths. Which images were transformed and which skipped (with the
one-word reason), the per-image verdicts with anything that drifted, the estimated cost
of the run, and the two paths: `santa/<ASIN>/christmas/` and
`santa/<ASIN>/contact-sheet.html`. The images are ready to upload as they are.

---

# Catalog mode: a whole store

Same engine, same rules, every product in the store, one folder per ASIN, and one page
that shows the store dressed. Two commands.

## C1: discover

Pass what the user pasted, as it came. Links from any Amazon marketplace, tracking junk and
all, app share links (`amzn.to`, `a.co`), commas or new lines between them:

```
python3 $S/catalog.py discover "<everything the user pasted>"
python3 $S/catalog.py discover my-products.txt                 # the same, in a file
python3 $S/catalog.py discover "Active+Listings+Report.txt"    # Seller Central, asin1 column
python3 $S/catalog.py discover "Active+Listings+Report.txt" --host www.amazon.co.uk
```

Every item becomes one ASIN on its own marketplace (a `.co.uk` link is fetched from
amazon.co.uk). A share link costs one HEAD request; its first redirect already names the
ASIN. Duplicates collapse. Anything that is not a product (a non-Amazon link, a store page,
a search page) is printed as `skipped ...: why` and the rest go ahead; relay the skipped
lines to the user.

Also accepted, second choice: the seller page `https://www.amazon.com/s?me=SELLERID` (walked
page by page, one attempt each, stops on the first bot check and keeps what it read), or the
`.html` of that page saved from the browser. A Brand Store or `/shop/` link is refused with
the words to ask for a list instead; relay them.

No Amazon at all: `discover path/to/store/` (its subfolders are one product each) or
`discover folderA folderB`. Each folder holds its images in gallery order by name (01 is the main
image); an optional `product.json` (`{"asin": "B0...", "title": "..."}`) names the product and its
output folder, else the folder name does. `run`, `status` and `index` work the same.

Writes `santa-catalog/<name>/catalog.json` and prints how many products were found, how many
were taken (default cap 50, in the order given; `--max 300` takes 300, `--max 0` all), and a
rough cost and time estimate, and the `run` line with a budget that covers it. Show those
lines to the user, then start C2 straight away with that `run` line. Do not stop to ask: this
is an autopilot skill and the budget is the answer. Only a user who asked for a dry run, or
named their own budget, changes that.

## C2: run

```
python3 $S/catalog.py run santa-catalog/my-store --budget 10
python3 $S/catalog.py run santa-catalog/my-store --budget 10 --dry-run     # fetch + classify, no spend
```

Phases, in order, each one resumable (run the same command again and it continues):

1. **Fetch.** Every listing, one attempt, polite pause. Three captchas in a row PAUSE the run
   with a message; the same command an hour later retries the blocked ones once.
2. **Dedupe.** An image already fetched for another ASIN (bundles, variations share
   galleries) is generated once and copied into every folder that uses it. No double spend.
3. **Classify.** The vision pass fills every plan.json. Duplicates never reach it.
4. **Cost gate.** Prints the count, the estimate (about $0.087 an image at medium), what was spent so far, the budget and an ETA. `--dry-run` stops here. Otherwise the
   run generates as many images as the budget covers, in catalog order, and names how many it
   held back. Nothing asks; the budget is the answer.
5. **Generate.** One pool, 4 workers across the whole store.
6. **Verify.** Every pair through the eight questions plus the measured framing check. Every failing image gets its one
   regeneration with the `regen_note` in the scene, then is verified again. Regenerations
   count against the budget too.
7. **Finish.** Duplicate outputs copied across, a `contact-sheet.html` and `report.md` per
   ASIN, then `index.html` and `report.md` at the top: the whole store, every Christmas image,
   hover for the original, a red `check` badge on anything not stage ready.

```
python3 $S/catalog.py status santa-catalog/my-store     # one line per product
python3 $S/catalog.py index  santa-catalog/my-store     # rebuild the top page from disk
```

Measured 26.9.2026 on a three-product store (13 gallery images, 9 unique): discover 3 s,
run about 2 minutes end to end, images about $0.30 at low.

## What a 300-product catalog does

Only arrives as a Seller Central report or a long paste, so the seller chose it. Discover
takes the first 50 in the order given and says how to take the rest (put the gift-worthy
sellers first). At four transforms a product, 300 products is 1,200 images, about $105 at
medium and about 3.5 hours. The run spends up to `--budget` and stops, so a $105 catalog
with a $35 budget does the first third and says so; the same command with `--budget 105`
finishes it. Listing pages are fetched one at a time with a pause, so 300 take
about 20 minutes, and three bot checks in a row pause the run instead of burning the address.

## What you tell the user at the end (catalog)

Four lines, then two paths. Products done of products found; Christmas images made and
how many passed every check; estimated cost and wall time; anything held back (budget,
blocks, listings with fewer than two images). Then open the page for them
(`open santa-catalog/<name>/index.html`) and give the path of the per-ASIN folders. The images
are ready to upload. Remind them that `originals/` is their way back in January.

Spot-check before you hand it over: open index.html, then open two or three
contact sheets and look at the product in each pair yourself. The gate is good; it is not you.

---

## Cost and quality

One run at `medium`; what comes out is ready to upload. Measured 26.9.2026 on the same four
frames at each quality (text, faces, props):

| quality | per image | per image, wall | what it looks like |
|---|---|---|---|
| low | $0.025 | 15 s | text identical; drawn elements (a Santa hat, skin) a little waxy |
| **medium (default)** | **$0.087** | **41 s** | natural fur and skin, the same text |
| high | $0.298 | 118 s | no visible gain over medium |

Text is identical at every quality because this is an edit: most pixels come from the
original. Do not run low as a preview and high as the final: a second run at another quality
is a NEW image, not a render of the one that was approved. `QUALITY=low` stays available for a
cheap first try on a product or two. Classify is about $0.002 per image and verify about $0.004
per pair on gpt-5.4. A listing with six secondary images is about $0.55 at medium. The estimate
uses published per-token rates; the OpenAI usage page has the exact figure.

## Amazon policy notes

- Main image stays white. This skill never writes to `01`.
- Amazon wants at least 1000 px on the longest side: 1024 passes. For a sharper zoom, upscale
  to 1500 or 2000 with any upscaler; a high-quality re-run does not add pixels.
- Seasonal images are secondary images. Sellers swap them in for Q4 and swap them out
  in January; keep `originals/` so they have the way back.
