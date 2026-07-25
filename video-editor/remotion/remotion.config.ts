// Remotion CLI config. Overlay comps (Captions, KineticIntro) have transparent
// backgrounds; render them with an alpha codec so they composite over footage:
//   npx remotion render Captions out.webm --codec=vp8   (alpha via yuva420p)
//   npx remotion render Captions out.mov  --codec=prores --prores-profile=4444
import { Config } from "@remotion/cli/config";

Config.setOverwriteOutput(true);
Config.setVideoImageFormat("png"); // keep alpha through the frame pipeline
