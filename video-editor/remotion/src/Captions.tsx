import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";

export type Word = { text: string; start: number; end: number };
export type CaptionsProps = {
  words: Word[];
  accent: string;
  fontSize?: number;
  perLine?: number;
};

// Word-by-word highlighted captions (Reels/Shorts/TikTok look), transparent
// background so the render composites over footage. Render with an alpha codec.
export const Captions: React.FC<CaptionsProps> = ({
  words,
  accent,
  fontSize = 96,
  perLine = 3,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  const lines: Word[][] = [];
  for (let i = 0; i < words.length; i += perLine) lines.push(words.slice(i, i + perLine));
  const line = lines.find((l) => t >= l[0].start && t <= l[l.length - 1].end);
  if (!line) return <AbsoluteFill />;

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: "0 8%" }}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "0 18px" }}>
        {line.map((w, i) => {
          const active = t >= w.start && t < w.end;
          const pop = active
            ? interpolate(t, [w.start, w.start + 0.12], [0.82, 1], { extrapolateRight: "clamp" })
            : 1;
          return (
            <span
              key={i}
              style={{
                fontFamily: "'DejaVu Sans', 'Arial', sans-serif",
                fontWeight: 800,
                fontSize,
                color: active ? accent : "white",
                WebkitTextStroke: "6px black",
                paintOrder: "stroke fill",
                transform: `scale(${pop})`,
                display: "inline-block",
                textTransform: "uppercase",
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const captionsCalculateMetadata = ({ props }: { props: CaptionsProps }) => {
  const end = Math.max(...props.words.map((w) => w.end), 1);
  return { durationInFrames: Math.ceil((end + 0.3) * 30) };
};
