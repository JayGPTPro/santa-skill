---
name: santa-skill
description: >-
  The Santa Skill. Turn an Amazon listing's secondary images into Christmas versions
  before Q4, with the product untouched and the main image left alone. One command in
  (an ASIN, an Amazon URL, or a folder of product images), a christmas/ folder plus a
  before/after contact sheet out. Zero questions during the run. Use when the user says
  "santa skill", "christmas images", "christmas mode", "Q4 images", "holiday listing
  images", "seasonal images", "xmas listing", or gives an ASIN together with christmas
  or holiday: "/santa-skill B0GYY18GYC", "make B0... christmas", "holiday versions of
  my listing images".
---

# The Santa Skill

One line in: `/santa-skill B0GYY18GYC`. Out: Christmas versions of the listing's
secondary images in `santa/<ASIN>/christmas/`, a before/after `contact-sheet.html`, and a
short `report.md`. The main image is never touched (Amazon wants it on pure white). The
product is never touched either: same shape, color, angle, label. Only the world around
it gets Christmas.

This is an AUTOPILOT skill. After the environment check passes, the run asks nothing.
The only stop is a missing input (Amazon blocked the fetch and no folder was given).
Every judgment call is yours; make it, log it in plan.json, keep moving.

Engine: OpenAI `gpt-image-2` through `images.edit`, the original photo as the input
image. Never Nano Banana, never Gemini, never any other image model, not as a fallback.
If gpt-image-2 is unavailable, stop and say so.

## Files

```
SKILL.md                              this file
scripts/check-env.sh                  prints OK / MISSING with exact fix lines
scripts/santa.py                      fetch, list, generate, regen, check, sheet
references/christmas-prompt-rules.md  the rules every prompt is built from, and the self-check
```

`S=~/.claude/skills/santa-skill/scripts` in the commands below. Run everything from the
user's working directory; the run lands in `<cwd>/santa/<ASIN>/`.

## Step 0: environment (first run on a machine, or after an error)

```
bash $S/check-env.sh
```

If anything prints MISSING, show the user the fix lines it printed and stop. Needs:
python3, the `openai` package, Pillow, curl, and `OPENAI_API_KEY` (env var, else
`~/Downloads/Claude/.env`). gpt-image-2 needs a verified OpenAI org.

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

## Step 2: classify (you look, the script lists)

```
python3 $S/santa.py list santa/B0GYY18GYC
```

This prints every original with its size and writes a `plan.json` template. Now OPEN
every file in `originals/` with the Read tool and fill in each entry:

- `role`: `main` | `lifestyle` | `packshot` | `infographic` | `logo`
- `action`: `transform` | `skip`
- `scene`: for transforms, one or two sentences in plain English describing what changes
  in THIS picture. Anchor it to what is there: name the surfaces, the people, the
  existing lights. Pick 1 or 2 items from the Christmas vocabulary in the rules file.
  When the image carries overlay text or icons, start the scene line with "Keep the
  headline '...' and the icons exactly as written, same position."
- `reason`: for skips, why (the report prints it).

Decision rules (full detail in `references/christmas-prompt-rules.md`):

- `01` (first in gallery) is the main image. Skip, always.
- Lifestyle scene, with or without a headline: transform. Most Amazon lifestyle images
  carry a headline; gpt-image-2 keeps short headlines intact, so a headline alone is
  not a reason to skip. Say so in the scene line.
- Text-heavy infographic (callouts, specs, diagrams, comparison charts, packing lists,
  dimension arrows): skip. Small callout text gets mangled. Note it so the seller can
  redo it in their design tool.
- Packshot on white as a secondary image: transform (the model builds a bright tabletop
  scene around it). Say "nothing near the product" in the scene line.
- Brand logo card or retail box as the subject: skip.

Aim for 3 to 6 transforms per listing. If a listing is nothing but infographics, say
that in the report rather than forcing it.

## Step 3: generate

```
python3 $S/santa.py generate santa/B0GYY18GYC
QUALITY=high python3 $S/santa.py generate santa/B0GYY18GYC     # for the real upload
```

Parallel (4 workers), quality `low` by default, ONE attempt per image, skips outputs that
already exist. Prompt = the fixed template from the rules file with your scene line
dropped in. Output size follows the original's aspect: square 1024x1024, landscape
1536x1024, portrait 1024x1536. Each call appends its token usage and a cost estimate to
`cost.jsonl`. Measured on the test listing: about 20 to 30 seconds per image, five images
in 40 seconds wall clock.

A moderation block or an API error is logged and skipped. No retry loop, ever.

## Step 4: self-check (you look again)

```
python3 $S/santa.py check santa/B0GYY18GYC
```

That prints mean brightness before vs after and flags any drop over 8 percent. It is the
number; the judgment is yours. OPEN every `christmas/*.png` next to its original and
answer the seven questions in the rules file: same product, nothing touching it, not
darker, no global tint, at most two props on a real surface, same person, no invented
text or logos. Also read the small text on the product's own screen or label: a digit
that changed is a note in the verdict.

Write into plan.json for each transform: `stage_ready: true|false` and a one or two
sentence `verdict` saying what changed and what, if anything, drifted.

One failing image gets ONE regeneration, with the failure written into the scene:

```
python3 $S/santa.py regen santa/B0GYY18GYC 06.jpg --note "The tent must stay orange. No ribbon on the product."
```

`regen` refuses a second retry by design. After it, the verdict says not stage ready and
why, and the run moves on.

## Step 5: contact sheet + report

```
python3 $S/santa.py sheet santa/B0GYY18GYC
```

Writes `contact-sheet.html` (self-contained, thumbnails embedded, product title on top,
one before/after row per image, skipped images shown with their reason, your verdict
under each row) and `report.md` (transformed, skipped and why, cost estimate per image
and per listing). Open the sheet for the user: `open santa/B0GYY18GYC/contact-sheet.html`.

## What you tell the user at the end

Four lines, then the paths. Which images were transformed and which skipped (with the
one-word reason), the per-image verdicts with anything that drifted, the estimated cost
of the run, and the two paths: `santa/<ASIN>/christmas/` and
`santa/<ASIN>/contact-sheet.html`. Remind them that `low` quality is for the preview and
that `QUALITY=high` is the setting for the files they upload.

## Cost

Measured 5.9.2026 on B0GYY18GYC, quality low, 1024x1024: about USD 0.025 per image
(1,500 input image tokens, 300 text tokens, 196 output tokens), USD 0.12 for five images.
A listing with six secondary images is about USD 0.15 at low and roughly USD 1 at high.
The estimate uses published per-token image rates; the OpenAI usage page has the exact
figure.

## Amazon policy notes

- Main image stays white. This skill never writes to `01`.
- Amazon wants at least 1000 px on the longest side: 1024 passes, but for a clean zoom
  re-run at `QUALITY=high` and, if the seller has an upscaler, upscale to 1500 or 2000.
- Seasonal images are secondary images. Sellers swap them in for Q4 and swap them out
  in January; keep `originals/` so they have the way back.
