import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";

export type CTAProps = { text: string; accent: string; sub?: string };

// Full-frame call-to-action end card (render as normal MP4/H.264).
export const CTAEndCard: React.FC<CTAProps> = ({ text, accent, sub }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 14 } });
  const scale = interpolate(s, [0, 1], [0.7, 1]);
  const op = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: "#0A0A0A", justifyContent: "center", alignItems: "center" }}>
      <div style={{ transform: `scale(${scale})`, opacity: op, textAlign: "center" }}>
        <div
          style={{
            fontFamily: "'DejaVu Sans', 'Arial', sans-serif",
            fontWeight: 900,
            fontSize: 120,
            color: "white",
            textTransform: "uppercase",
            padding: "24px 56px",
            background: accent,
            borderRadius: 28,
          }}
        >
          {text}
        </div>
        {sub ? (
          <div style={{ marginTop: 32, fontFamily: "'DejaVu Sans', sans-serif", fontSize: 46, color: "white", opacity: 0.82 }}>
            {sub}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
