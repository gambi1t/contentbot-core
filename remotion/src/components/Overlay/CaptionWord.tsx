/**
 * CaptionWord — крупное одно слово накладывается над лицом
 * в момент когда ведущий его произносит.
 *
 * Hormozi / Submagic стиль: bold uppercase, hot color, scale-bounce reveal.
 *
 * Props:
 *   - text: само слово (1-3 слова максимум)
 *   - color: цвет акцента
 *   - position: куда (lower-third / center / upper-third)
 */
import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import {
  SPRING,
  FONT_SIZE,
  FONT_WEIGHT,
  ACCENTS,
  EASING,
  LETTER_SPACING,
} from "../../design-tokens";
import { interTight } from "../../fonts";

export type CaptionWordProps = {
  text: string;
  color?: keyof typeof ACCENTS;
  position?: "lower-third" | "center" | "upper-third";
  [key: string]: unknown;
};

export const CaptionWord: React.FC<CaptionWordProps> = ({
  text,
  color = "hot",
  position = "lower-third",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Spring entry
  const scale = spring({
    frame,
    fps,
    config: SPRING.heavy,
  });

  // Subtle wiggle for emphasis (10% rotation oscillation)
  const wiggle = Math.sin(frame * 0.4) * 1.5;

  // Exit: last 8 frames fade
  const exitOpacity = interpolate(
    frame,
    [durationInFrames - 8, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASING.fastOut },
  );

  // Vertical position
  const positionStyles: Record<string, React.CSSProperties> = {
    "lower-third": { bottom: "20%", left: 0, right: 0 },
    "center": { top: "50%", left: 0, right: 0, transform: "translateY(-50%)" },
    "upper-third": { top: "20%", left: 0, right: 0 },
  };

  const accentColor = ACCENTS[color];

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          ...positionStyles[position],
          textAlign: "center",
          opacity: exitOpacity,
        }}
      >
        <div
          style={{
            display: "inline-block",
            padding: "20px 40px",
            backgroundColor: "rgba(0,0,0,0.85)",
            color: accentColor,
            fontSize: FONT_SIZE.heroXL,
            fontFamily: interTight,
            fontWeight: FONT_WEIGHT.black,
            letterSpacing: LETTER_SPACING.hero,
            lineHeight: 1,
            textTransform: "uppercase",
            borderRadius: 16,
            transform: `scale(${scale}) rotate(${wiggle}deg)`,
            transformOrigin: "center",
            boxShadow: `0 0 80px ${accentColor}80, 0 30px 60px rgba(0,0,0,0.6)`,
            border: `4px solid ${accentColor}`,
            textShadow: `0 0 20px ${accentColor}60`,
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
};
