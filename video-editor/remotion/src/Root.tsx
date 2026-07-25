import React from "react";
import { Composition } from "remotion";
import { Captions, captionsCalculateMetadata } from "./Captions";
import { CTAEndCard } from "./CTAEndCard";
import { KineticIntro } from "./KineticIntro";

const FPS = 30;
const W = 1080;
const H = 1920;

// Sample props so `remotion studio` previews immediately. Real renders pass
// --props='{...}' (see remotion/README.md).
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Captions"
        component={Captions}
        fps={FPS}
        width={W}
        height={H}
        durationInFrames={90}
        calculateMetadata={captionsCalculateMetadata}
        defaultProps={{
          accent: "#00E5FF",
          words: [
            { text: "Stop", start: 0.0, end: 0.45 },
            { text: "scrolling", start: 0.45, end: 1.0 },
            { text: "watch", start: 1.0, end: 1.5 },
            { text: "this", start: 1.5, end: 2.0 },
          ],
        }}
      />
      <Composition
        id="CTAEndCard"
        component={CTAEndCard}
        fps={FPS}
        width={W}
        height={H}
        durationInFrames={48}
        defaultProps={{ text: "Подпишись", accent: "#00E5FF", sub: "" }}
      />
      <Composition
        id="KineticIntro"
        component={KineticIntro}
        fps={FPS}
        width={W}
        height={H}
        durationInFrames={60}
        defaultProps={{ text: "НОВИНКА", accent: "#00E5FF" }}
      />
    </>
  );
};
