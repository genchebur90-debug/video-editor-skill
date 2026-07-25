// Render a composition to a file. Runs where headless Chromium is available.
//
//   node render.mjs <CompId> <out.(mov|webm|mp4)> [props.json]
//
// Alpha overlays (Captions, KineticIntro): use .webm (vp8) or .mov (prores 4444)
// so they composite over footage via the FFmpeg engine, e.g.:
//   python3 ../ops.py overlay-video base.mp4 out.webm final.mp4 --position center
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition, ensureBrowser } from "@remotion/renderer";
import path from "path";
import fs from "fs";

const [, , comp = "Captions", out = "out.mov", propsFile] = process.argv;
const inputProps = propsFile ? JSON.parse(fs.readFileSync(propsFile, "utf8")) : {};

await ensureBrowser(); // downloads Chrome Headless Shell on first run

const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
const composition = await selectComposition({ serveUrl, id: comp, inputProps });

const isWebm = out.endsWith(".webm");
const isMov = out.endsWith(".mov");
const codec = isWebm ? "vp8" : isMov ? "prores" : "h264";
const extra = isWebm ? { pixelFormat: "yuva420p" } : isMov ? { proResProfile: "4444" } : {};

await renderMedia({ composition, serveUrl, codec, outputLocation: out, inputProps, ...extra });
console.log(JSON.stringify({ ok: true, comp, out, codec }));
