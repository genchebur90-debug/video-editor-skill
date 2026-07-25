import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";

export type IntroProps = { text: string; accent: string };

// Kinetic word-by-word intro title, transparent background (composite over
// footage with an alpha codec, or use full-frame over a colour).
export const KineticIntro: React.FC<IntroProps> = ({ text, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const words = text.split(" ");

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", gap: 16 }}>
      {words.map((w, i) => {
        const delay = i * 6;
        const s = spring({ frame: frame - delay, fps, config: { damping: 12 } });
        const y = interpolate(s, [0, 1], [90, 0]);
        const op = interpolate(s, [0, 1], [0, 1]);
        return (
          <div
            key={i}
            style={{
              transform: `translateY(${y}px)`,
              opacity: op,
              fontFamily: "'DejaVu Sans', 'Arial', sans-serif",
              fontWeight: 900,
              fontSize: 130,
              color: i % 2 ? accent : "white",
              WebkitTextStroke: "4px black",
              paintOrder: "stroke fill",
              textTransform: "uppercase",
            }}
          >
            {w}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
