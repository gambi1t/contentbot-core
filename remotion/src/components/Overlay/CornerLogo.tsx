/**
 * CornerLogo — floating логотип AI-инструмента в углу talking-head.
 *
 * Рендерится в alpha-WebM, накладывается ffmpeg-overlay поверх HeyGen-аватара.
 * Эстетика: Кайсар / Rowan Cheung — иконка появляется на 2-3 сек когда ведущий
 * упоминает инструмент, потом исчезает.
 *
 * Spring-bounce на entry, fade-out на exit.
 *
 * Props:
 *   - emoji: иконка (можно SVG path как character — 🚀 / 🔭 / ⚡)
 *   - label: подпись под иконкой (опционально)
 *   - corner: где рисовать (top-right / top-left / bottom-right / bottom-left)
 *   - color: акцент-цвет иконки (из ACCENTS)
 */
import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import {
  SAFE_AREA,
  SPRING,
  FONT_SIZE,
  FONT_WEIGHT,
  ACCENTS,
  EASING,
} from "../../design-tokens";
import { interTight } from "../../fonts";

export type CornerLogoProps = {
  emoji: string;
  label?: string;
  corner?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
  color?: keyof typeof ACCENTS;
  [key: string]: unknown;
};

export const CornerLogo: React.FC<CornerLogoProps> = ({
  emoji,
  label,
  corner = "top-right",
  color = "warm",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Entry spring
  const enterScale = spring({
    frame,
    fps,
    config: SPRING.default,
  });

  // Exit fade-out (последние 12 frames)
  const exitOpacity = interpolate(
    frame,
    [durationInFrames - 12, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASING.fastOut },
  );

  const finalOpacity = enterScale * exitOpacity;
  const finalScale = enterScale;

  // Position by corner
  const positions: Record<string, React.CSSProperties> = {
    "top-right": { top: SAFE_AREA, right: SAFE_AREA },
    "top-left": { top: SAFE_AREA, left: SAFE_AREA },
    "bottom-right": { bottom: SAFE_AREA, right: SAFE_AREA },
    "bottom-left": { bottom: SAFE_AREA, left: SAFE_AREA },
  };

  const accentColor = ACCENTS[color];

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          ...positions[corner],
          opacity: finalOpacity,
          transform: `scale(${finalScale})`,
          transformOrigin: corner.includes("right") ? "right" : "left",
        }}
      >
        {/* Glowing icon container */}
        <div
          style={{
            width: 160,
            height: 160,
            borderRadius: 32,
            backgroundColor: "rgba(0,0,0,0.7)",
            border: `3px solid ${accentColor}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 80,
            boxShadow: `0 0 60px ${accentColor}60, 0 20px 40px rgba(0,0,0,0.5)`,
            backdropFilter: "blur(12px)",
          }}
        >
          {emoji}
        </div>

        {/* Optional label */}
        {label && (
          <div
            style={{
              marginTop: 16,
              textAlign: "center",
              color: "#ffffff",
              fontSize: FONT_SIZE.body,
              fontFamily: interTight,
              fontWeight: FONT_WEIGHT.bold,
              letterSpacing: 0.5,
              textShadow: "0 2px 8px rgba(0,0,0,0.8)",
            }}
          >
            {label}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
