#!/usr/bin/env node
/**
 * Turns the original photos, unboxing frames and logo into light web files.
 *
 *   assets-original/photos/*.{jpg,jpeg,png,webp}  ->  public/images/<name>.webp   (max 1600 px wide)
 *   assets-original/cards/*.{jpg,jpeg,png,webp}   ->  public/images/<name>.webp   (max 800 px wide)
 *   assets-original/unboxing/ezgif-frame-001.jpg… ->  public/media/unboxing.mp4 + .webm + -poster.webp
 *   assets-original/logo.svg | logo.png           ->  public/logo.svg | public/logo.webp (<= 30 KB)
 *
 * Originals are never modified or deleted. Prints before/after weight.
 * Usage: node scripts/optimize-media.mjs
 */
import { execFileSync } from "node:child_process";
import { copyFile, mkdir, readdir, stat } from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const root = path.resolve(import.meta.dirname, "..");
const src = path.join(root, "assets-original");
const outImg = path.join(root, "public", "images");
const outMedia = path.join(root, "public", "media");
const IMG = /\.(jpe?g|png|webp)$/i;
const kb = (b) => `${(b / 1024).toFixed(0)} KB`;
let before = 0;
let after = 0;

async function list(dir, re) {
  try {
    return (await readdir(dir)).filter((f) => re.test(f)).sort();
  } catch {
    return [];
  }
}

async function photos(folder, maxWidth) {
  for (const f of await list(path.join(src, folder), IMG)) {
    const input = path.join(src, folder, f);
    const name = f.replace(IMG, "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const output = path.join(outImg, `${name}.webp`);
    // Step quality down until the file is under 200 KB (or quality hits 55).
    for (let q = 80; q >= 55; q -= 5) {
      await sharp(input).rotate().resize({ width: maxWidth, withoutEnlargement: true }).webp({ quality: q }).toFile(output);
      if ((await stat(output)).size <= 200 * 1024) break;
    }
    const [a, b] = [(await stat(input)).size, (await stat(output)).size];
    before += a;
    after += b;
    console.log(`${folder}/${f}  ${kb(a)} -> images/${name}.webp ${kb(b)}`);
  }
}

async function unboxing() {
  const dir = path.join(src, "unboxing");
  const frames = await list(dir, /^ezgif-frame-\d+\.jpe?g$/i);
  if (!frames.length) return;
  for (const f of frames) before += (await stat(path.join(dir, f))).size;
  const ext = path.extname(frames[0]);
  const pattern = path.join(dir, `ezgif-frame-%03d${ext}`);
  const scale = "scale='min(1280,iw)':-2";
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", "30", "-i", pattern, "-vf", scale, "-c:v", "libx264", "-crf", "26", "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", path.join(outMedia, "unboxing.mp4")]);
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", "30", "-i", pattern, "-vf", scale, "-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-an", path.join(outMedia, "unboxing.webm")]);
  await sharp(path.join(dir, frames[0])).resize({ width: 1280, withoutEnlargement: true }).webp({ quality: 75 }).toFile(path.join(outMedia, "unboxing-poster.webp"));
  for (const f of ["unboxing.mp4", "unboxing.webm", "unboxing-poster.webp"]) {
    const s = (await stat(path.join(outMedia, f))).size;
    if (f !== "unboxing.webm") after += s; // the browser loads one of mp4/webm
    console.log(`unboxing: ${frames.length} frames -> media/${f} ${kb(s)}`);
  }
}

async function logo() {
  const svg = path.join(src, "logo.svg");
  const png = path.join(src, "logo.png");
  try {
    await stat(svg);
    await copyFile(svg, path.join(root, "public", "logo.svg"));
    console.log("logo.svg copied");
    return;
  } catch {}
  try {
    const a = (await stat(png)).size;
    const out = path.join(root, "public", "logo.webp");
    for (let w = 512; w >= 128; w -= 64) {
      await sharp(png).resize({ width: w, withoutEnlargement: true }).webp({ quality: 85 }).toFile(out);
      if ((await stat(out)).size <= 30 * 1024) break;
    }
    const b = (await stat(out)).size;
    before += a;
    after += b;
    console.log(`logo.png ${kb(a)} -> logo.webp ${kb(b)}`);
  } catch {}
}

await mkdir(outImg, { recursive: true });
await mkdir(outMedia, { recursive: true });
await photos("photos", 1600);
await photos("cards", 800);
await unboxing();
await logo();
console.log(before ? `\nTotal: ${kb(before)} -> ${kb(after)} (${Math.round((1 - after / before) * 100)}% lighter)` : "Nothing found in assets-original/. See docs/IMAGE_BRIEF.md.");
