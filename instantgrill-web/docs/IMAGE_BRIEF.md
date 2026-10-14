# Instant Grill: image brief

Every image slot, with its full shot description, lives in `src/lib/catalog.ts` under `images`.
In development each empty slot shows its brief on screen, so you can see what goes where.

## Look

- Dark, cinematic, warm. The light comes from the coals: orange rim light, deep shadows.
- Real Pakistani settings: rooftops, terraces, northern-area road trips. Real food: tikka, seekh, corn.
- The product must be **your actual grill**. Shoot it, or give a clean product photo to an AI tool as
  the reference image so the grill in every scene matches what customers receive.
- No text, logos or badges baked into photos. The site adds type.

## Slots

| Key | Used in | Aspect | File to save |
|---|---|---|---|
| `hero` | Hero, shop gallery | 4:5 | `public/images/hero.webp` |
| `fastStart` | Why: coals ready fast | 16:10 | `public/images/fast-start.webp` |
| `portable` | Why: packs into one case, gallery | 4:5 | `public/images/portable.webp` |
| `clean` | Why: ash tray, gallery | 1:1 | `public/images/clean.webp` |
| `build` | Why: built to last, gallery | 1:1 | `public/images/build.webp` |
| `family` | Why: real charcoal taste, final banner | 16:9 | `public/images/family.webp` |
| `howLoad` | How it works: Load | 4:3 | `public/images/how-load.webp` |
| `howLight` | How it works: Light | 4:3 | `public/images/how-light.webp` |
| `howGrill` | How it works: Grill | 4:3 | `public/images/how-grill.webp` |

Export WebP, about 2000px on the long side, under 400 KB each. Then set `src` for that slot,
for example `src: "/images/hero.webp"`. Every image animates automatically through `MotionImage`.
