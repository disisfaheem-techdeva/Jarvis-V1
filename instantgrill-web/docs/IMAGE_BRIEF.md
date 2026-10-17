# Instant Grill: images

Use the **real box photos you already have**. No AI generation is planned. Before using any
AI-made image, check the box lid for wrong text (phone number, spelling).

## Where they go

Put files in `public/images/`, then set `src` for the slot in `src/lib/catalog.ts`,
for example `src: "/images/hero.webp"`. Every image animates automatically.

| Slot (in `images`) | Used in | Shape |
|---|---|---|
| `hero` | Home hero | 4:5 portrait, open box, skewers on the grill |
| `everything` | "In every box" | 16:10 flat lay of all contents |
| `rooftop` | Why us, final banner | 4:5 |
| `fresh` | Why us | 1:1 marinated meat in the box |
| `noMess` | Why us | 1:1 |
| `dawat` | Why us (wide) | 16:9 family or group |
| `stepOrder`, `stepDeliver`, `stepLight`, `stepGrill` | How it works | 4:3 each |
| Each product's `image` | Menu cards, product page | 4:3 |

## Prepare

- WebP, max 1600 px wide (cards 800 px), under 200 KB each. Keep originals in `assets-original/`.
- The current site's ~180 unboxing JPG frames should become one MP4/WebM under 2 MB:
  `ffmpeg -framerate 30 -i ezgif-frame-%03d.jpg -c:v libx264 -crf 26 -pix_fmt yuv420p -movflags +faststart unboxing.mp4`
- Logo: SVG if the source exists, otherwise WebP under 30 KB.

This build machine cannot download from instantgrill.shop, so add the files to the repo
(or attach them in the chat) and they will be wired in.
