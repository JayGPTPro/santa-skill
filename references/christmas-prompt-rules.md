# Christmas prompt rules

Source: Jay's original Christmas prompt (the one he refined with small strategies) plus the product rule from his seasonal talk. These rules are non-negotiable. Every prompt
the skill sends to gpt-image-2 is built from them, and every output is judged against them.

## The one idea

The product is the thing being sold. Christmas happens AROUND it, never TO it. A shopper
must be able to put the seasonal image next to the main image and see the identical product.

## Rules

1. Product untouched. Same shape, color, angle, size, position, label text, packaging.
   Only the environment changes.
2. Zero color filters. No red or green tint over the whole image. Seasonal color lives
   only in background elements (tree, gifts, lights, wreath).
3. Max 3 props in total (gifts, candles, greenery). Props sit only on an existing surface,
   at the far side from the product, never next to, on or touching it. If there is no
   natural place for a prop, no props. (Decided 2026-09-05: talk said 2, prompt said "a few".)
4. People stay EXACTLY as they are: same face, pose, hands and clothing. No sweaters, no
   scarves. The one allowed change: one natural red Santa hat on ONE adult, matching head
   shape, shadow and lighting. Never on a child, never on the product.
5. Never darken the image. Christmas is warm and bright. The output must be as bright as
   the original or brighter. No moody, dim, candlelit look.
6. Christmas vocabulary: warm lights, soft bokeh, light garland, small greenery, candles,
   a few wrapped gift boxes. A tree ONLY when the scene is clearly a home living room, and
   then exactly one, placed naturally. Outdoors, kitchens, campsites, studios: no tree.
   No snow overlays, no snow on ground or objects. Snow only as a view through a window.
   No fake sparkles, no glitter, no floating objects, no cartoon items.
   If the image carries graphic elements (icons, badges), soft Christmas graphics are allowed
   around them. Never a Santa hat, bow, ribbon or ornament on the product itself.
7. Photorealistic. Matches the original photo's lens, lighting direction, camera height
   and depth of field.
8. No added text, logos, badges or watermarks.

## Which images get transformed

- Main image: never. Amazon requires a pure white background. Skip.
- Lifestyle / product in a scene: yes. This is where Christmas sells.
- Product on white with no scene (secondary packshots): yes if a scene can be built
  around it, otherwise skip. Default: transform, the model builds a tabletop scene.
- Text-heavy infographic (callouts, comparison charts, feature bullets): skip by default.
  Generation mangles the text. Note it in the report so the seller can re-do it in
  their design tool with a seasonal background.
- Image with a brand logo as the main content: skip.
- Color-variant packshots (the same product in other colors on white, usually a run of
  near-identical images at the end of the gallery): skip. Each is the main image of a
  sibling ASIN and main images stay white. Say so in the report.
- Multi-panel collages: skip. There is no single scene to decorate.
- Summer scenes (beach, pool, shorts, sun): transform, but keep it to one or two tiny
  accents and mark the image "summer scene" in the report. Clothing never changes, so a
  heavy Christmas treatment on a beach reads wrong to cold-climate shoppers; a light touch
  reads as "holiday in the sun". The seller decides whether to use it.

## Prompt template

The script builds every prompt from this template. `{scene}` is the only variable and is
written per image in plan.json by the agent after looking at the picture.

```
Create a clean premium Christmas version of this exact image. Keep the original photo fully
intact with no changes to people, faces, clothing, poses, hands, product shape, materials,
colors, lighting, shadows, depth, or camera angle. Keep every existing headline, label and
graphic element exactly as written. Add only subtle realistic holiday accents that fit
naturally. Use warm lights, soft bokeh, light garland, small greenery, candles, or wrapped
gift boxes: at most three added props in total, placed on an existing surface at the far
side from the product, never next to it. Do not add Christmas trees unless the scene is clearly a home living
room. In a living room you may add one realistic tree placed naturally. In any other
location do not add any trees. All additions must match the real lighting, shadows, and
color tones. Keep everything photorealistic with no cartoon items, fake sparkles, snow
overlays, glitter, or floating objects. No snow on the ground or on objects; snow may
appear only as a view through a window. If adults appear you may add one natural red Santa
hat to one adult. The hat must match head shape, shadow, and lighting. If the image includes
graphic elements you may add soft Christmas graphics or elements. Nothing may be placed on
or touching the product. The result should look naturally decorated for Christmas. Warm,
elegant and subtle.
Scene notes for this image: {scene}
```

## Writing a good `{scene}` line

Look at the original first. Name what is already there and what changes, in one or two
sentences. Good examples:

- "Home living room: one realistic tree softly out of focus by the window, a light garland
  on the shelf, two wrapped gifts on the floor beside the sofa. Santa hat on the man."
- "Kitchen, not a living room: no tree. Small greenery and a candle on the counter to the
  right, well away from the product. Warm lights in the bokeh."
- "Outdoor backyard at night: no tree, no snow. Garland along the fence, warm lights already
  there stay, two wrapped gift boxes on the table. Santa hat on one adult."
- "Plain white packshot: keep the white studio feel; add a small sprig of greenery and one
  wrapped gift box on the surface far from the product, soft warm bokeh behind."

Bad scene lines: "make it Christmassy" (no anchor), "add a red glow" (a filter),
"put a bow on it" (touches the product), "cozy candle light" (darkens).

## Self-check after generation

For each before/after pair, look at both and answer:

1. Same product? Shape, color, angle, size, label text. Any drift = FAIL.
2. Anything on or touching the product? = FAIL.
3. Darker than the original? = FAIL.
4. Global tint? = FAIL.
5. More than two props, or a prop floating in air? = FAIL.
6. Person changed (face, pose, clothing)? = FAIL. More than one Santa hat, or a hat on a child? = FAIL.
8. Tree outside a living room, or snow overlay, or glitter? = FAIL.
7. Generated text or logos? = FAIL.

One regeneration per failing image, with the failure written into the scene line
("the product must stay matte black, do not add a ribbon"). After that, report it as
not stage-ready and move on.

## Decisions log (Jay, 2026-09-05)
Where the seasonal talk and Jay's Christmas prompt disagreed, the prompt won on four and a
merge won on one:
- Clothing: unchanged (talk said sweaters).
- Snow: only through a window (talk liked snow outside; prompt banned overlays).
- Tree: only in a home living room, exactly one (talk had a tree almost everywhere).
- Santa hat: one, on one adult (talk said none).
- Props: max 3, at the far side from the product (talk said 2, prompt said "a few").
