# Christmas prompt rules

Source: Jay's Christmas prompt, refined into the STRONG and WINTER versions he chose on 5.9.2026 for
the Cyprus talk, and moved into this skill on 26.9.2026. Every prompt the skill sends to gpt-image-2
is built from these rules, and every output is judged against them.

## The one idea

The product is the thing being sold. Christmas happens AROUND it, never TO it. A shopper must be
able to put the seasonal image next to the main image and see the identical product. And the
Christmas must be unmistakable: it has to read as Christmas at Amazon thumbnail size, where the
subtle version disappeared.

## What never moves (KEEP, in every prompt)

1. The product: same shape, color, size, position, angle, label and packaging. Nothing added on
   it, in front of it or touching it. Things that were already on it (a drink on a cooler) stay.
2. Every headline, label and graphic element, exactly as written, same position.
3. Every person's face, identity, pose, hands and expression.
4. The camera angle, framing and lens.
5. Photorealistic, matching light and shadows. No cartoon items, no glitter, no floating objects,
   no added text or logos, no sparkle or snow overlay pasted flat over the photo.
6. As bright as the original or brighter. Warm and festive, never moody or dim. No color filter.

## What Christmas looks like (three modes, chosen per image)

**scene** (a lifestyle photo): ONE large anchor plus 3 or 4 supporting elements, warm string lights
used generously. Anchor: a tall, fully decorated, lit Christmas tree in any home interior; elsewhere a
big lit wreath, a lit garland along a shelf, railing or fence, or a small decorated evergreen outdoors.
Supporting: lit garland, wrapped gifts on the floor or a far surface, stockings, a wreath, snow falling
outside a window, a light dusting of snow on outdoor ground. Clothing stays unchanged.

**winter** (a summer or warm-weather scene: a sunny patio, a backyard, people in summer
clothes): the scene becomes full winter. Bright overcast winter light, fresh snow on the ground and
outdoor surfaces, bare or snow-dusted trees. The same people in the same poses get warm winter clothing
that fits them (knit sweaters, jackets, beanies, scarves, boots). Summer drinks and props that are
not the product may become winter ones. Then the Christmas additions. This is the one place clothing
changes (Jay, 5.9: a tank top in a Christmas image reads wrong; snow is the fastest Christmas cue).

**sun** (people in the water, at a pool or on a beach, or in swimwear): Christmas in the sun. Never snow,
never winter clothing: the same people keep their swimwear and summer clothes, and the sunshine, water,
sand and pool deck stay. Warm string lights generously, tinsel, a small decorated palm or Christmas tree on
the deck or sand, wrapped gifts on the far side, red and gold accents. This wins over winter (Jay, 27.9:
people in a pool or on a beach wearing coats in the snow is absurd). The classifier picks it, and the code
turns any winter pick whose `seen` line names a pool, a beach, the sea or swimwear into sun.

Santa hat, in every mode: at most one, on one adult, named in the scene line; never on a child. A
beanie or other winter hat is clothing, not a Santa hat.

## Which images get transformed

Lifestyle images only: the product in a real setting, with or without people, with or without a
headline band. Everything else is skipped and named in the report:

- Main image: never. Amazon requires a pure white background.
- The product on a plain white or studio background, in any form: "product on white". (Jay, 26.9.)
- A color variant on white: "color variant". It is the main image of a sibling listing.
- Text-heavy infographic (five or more text elements, bullets, specs, arrows, diagrams, text across the
  product): generation mangles small text; the seller can redo it in a design tool.
- Brand logo card, brand graphic, retail box, multi-panel collage, several colors side by side.

## Prompt templates

In `scripts/santa.py`: `STRONG_TEMPLATE`, `WINTER_TEMPLATE` and `SUN_TEMPLATE`, each ending in `KEEP`. `{scene}` is
the only variable, written per image in plan.json after looking at the picture; `mode` picks the
template.

## Writing a good `{scene}` line

Say what gets added and WHERE, anchored to what is in the picture, and what must stay exactly.

- "A fully decorated Christmas tree with warm lights clearly visible behind the sofa on the right, a
  lit garland with bows along the windowsill, three wrapped gift boxes on the floor to the far left, and
  snow falling outside the window. Keep the dog and the gray dog bed exactly as they are, nothing on
  the bed."
- "Warm string lights along the whole edge of the orange tent and between the trees above, a large
  evergreen wreath with a red bow on the tent door, three wrapped gifts beside the tent, a light dusting
  of snow on the ground. One natural red Santa hat on the father only. Keep the tent orange."
- (winter) "Warm string lights along the fence, a decorated small evergreen with lights in the corner of
  the patio, two wrapped gift boxes on the far side. Keep the drinks on the cooler lid."
- (sun) "Warm string lights and gold tinsel along the pool fence, a small decorated palm with red and gold
  ornaments at the far pool corner, three wrapped gifts on the far deck. One natural red Santa hat on the
  man singing only. Keep everyone's swimwear and the float rings exactly as they are."

Bad: "make it Christmassy" (no anchor), "add a red glow" (a filter), "put a bow on it" (touches the
product), "Santa hats on everyone".

## Self-check after generation

1. Same product? Shape, color, angle, size, label text. Any drift = FAIL.
2. Anything ADDED on, in front of or touching the product, or the product hidden? = FAIL.
3. Darker than the original? = FAIL.
4. Global tint? = FAIL.
5. Anything floating, or resting on nothing? = FAIL.
6. A face, pose or hands changed, clothing changed outside winter mode, more than one Santa hat, or a
   hat on a child? = FAIL.
7. Generated text or logos, or a headline changed? = FAIL.
8. Glitter, cartoon items, a pasted overlay, or any snow or frost in sun mode? = FAIL.
9. Not obviously Christmas at thumbnail size? = FAIL.
10. Product smaller or moved in the frame? MEASURED by the script (scale under 0.88 or a shift over
    12 percent of the width) = FAIL. In winter mode the whole scene changes, so a low-confidence match
    is left to the eye and question 1.

One regeneration per failing image, with the failure written into the scene line. After that, report
it as not stage-ready and move on.

## Decisions log (Jay, 2026-09-05, superseded 26.9 by the STRONG/WINTER rules above)
Where the seasonal talk and Jay's Christmas prompt disagreed, the prompt won on four and a
merge won on one:
- Clothing: unchanged (talk said sweaters).
- Snow: only through a window (talk liked snow outside; prompt banned overlays).
- Tree: only in a home living room, exactly one (talk had a tree almost everywhere).
- Santa hat: one, on one adult (talk said none).
- Props: max 3, at the far side from the product (talk said 2, prompt said "a few").

## Decisions log (26.9.2026, catalog mode)
- The two looking passes (classify, verify) run on gpt-5.4 vision, so a store of 300 products
  does not need 2,000 agent looks. gpt-5.4-mini flipped one lifestyle frame to infographic
  between two runs on the same gallery; gpt-5.4 gave the same eight answers twice.
- Definitions the verify pass uses. A PROP is a discrete added object: each gift box, a candle,
  a bundle of greenery. String lights, bokeh, a garland along an edge and a wreath on a far wall
  are vocabulary, not props. A TREE is a full standing conifer with ornaments; a garland or a
  sprig is not a tree. Before these definitions the gate failed three of four good frames
  (it counted a garland as a tree and lights as props).
- The gate tolerates four props and names the count in the verdict; five fails. The rule for
  the prompt stays three.
- A headline plus a sub-line in a colored band is a lifestyle frame, not an infographic.
  Infographic means five or more text elements, bullets/specs/arrows/diagrams, or text across
  the product's body.
- 26.9 later, from three stores (Melissa & Doug, Lodge, Dash, 185 images at low): a packshot whose
  aspect does not match the output size gets reframed and shrinks. Fix: pad to the output
  aspect with the border colour, crop back. And the size check is MEASURED (template match,
  scale under 0.88 fails), never asked of the model. The verify pass also gets the classifier's
  `seen` line, so it knows a sofa-and-rug room is a living room where one tree is allowed.
- Disney-licensed products (Mickey, princess plates) are refused by OpenAI moderation. Logged,
  skipped, no retry; the report names them.
- 26.9, packshots: even with pad-and-crop, ten packshots shrank to 75-88 percent because the old
  rule told the model to build a tabletop scene. A camera-lock sentence at the start of every
  packshot scene (enforced in code) kept all ten at full size at low quality; high was not
  needed. Tight packshots and color variants are now skipped: with the lock they only get a
  sprig in a corner, and a color variant is another listing's main image. On two Lodge
  galleries that cut the transforms from 9 to 3-4 and from 8 to 3.
- 26.9, quality: one run at medium, ready to upload (Jay). Measured on four frames: low $0.025 /
  15 s, medium $0.087 / 41 s, high $0.298 / 118 s. Text identical at all three; low draws hats
  and skin waxier; high adds nothing visible. No low-preview-then-high flow: the second run is a
  new image.
- 26.9, Jay chose the rich version for the skill itself ("I want it to do these things, change the
  clothes to fit the weather"): the STRONG and WINTER prompts he approved for Cyprus on 5.9 replace
  the subtle rules. Tested on nine images at medium (the Cyprus patio, dog and mixer, and six from the
  three demo stores): all read as Christmas at thumbnail size; the patio became full winter with the
  same people in coats and the cooler untouched. The verifier needed two definitions to stop false
  fails: "nothing ADDED on the product" (the drinks were on the cooler in the original) and "a beanie
  is clothing, not a Santa hat". It still caught a real drift on a packshot (the dashboard moved down
  and its printed logo was redrawn).
- 26.9, Jay: Santa dresses lifestyle images only. Every product-on-white image skips ("product on
  white"), enforced in code as well as in the classifier, so the packshot mode and its camera lock
  are gone. Tested on three galleries: every white-background frame skipped, every lifestyle frame
  transformed.
