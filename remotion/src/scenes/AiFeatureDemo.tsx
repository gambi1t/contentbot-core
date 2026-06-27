/**
 * AiFeatureDemo — первая прод-сцена для @panferovai_contentbot.
 *
 * Сценарий (8 сек, 240 frames @ 30fps, 1080×1920 9:16):
 *   0.0–0.8с (frames 0-24)   — fade-in браузер mockup
 *   0.8–2.0с (frames 24-60)  — в адресной строке появляется claude.ai
 *   2.0–4.5с (frames 60-135) — Typewriter промпта в chat input
 *   4.5–8.0с (frames 135-240) — стриминг ответа по словам
 *
 * Стиль: Постулат (тёмный фон + оранжевый accent + Inter Tight).
 * Используется как B-roll вставка в talking-head ролик.
 *
 * Props:
 *   - product: string  (default "Claude Opus 4.7")
 *   - feature: string  (default "extended thinking")
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

export type AiFeatureDemoProps = {
  product: string;
  feature: string;
  // Index signature нужен для Remotion <Composition> generic constraint
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

export const AiFeatureDemo: React.FC<AiFeatureDemoProps> = ({ product, feature }) => {
  const frame = useCurrentFrame();

  // Phase 1: browser fade-in (0-24)
  const browserOpacity = interpolate(frame, [0, 24], [0, 1], {
    extrapolateRight: "clamp",
  });
  const browserY = interpolate(frame, [0, 24], [40, 0], {
    extrapolateRight: "clamp",
  });

  // Phase 2: URL typing (24-60)
  const url = "claude.ai";
  const urlChars = Math.floor(interpolate(frame, [24, 60], [0, url.length], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  }));
  const visibleUrl = url.slice(0, urlChars);

  // Phase 3: prompt typewriter (60-135)
  const prompt = `Расскажи про ${product} и его новую фичу — ${feature}`;
  const promptChars = Math.floor(interpolate(frame, [60, 135], [0, prompt.length], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  }));
  const visiblePrompt = prompt.slice(0, promptChars);

  // Phase 4: response streaming word-by-word (135-240)
  const responseWords = [
    product,
    "—",
    "новая",
    "версия",
    "флагманской",
    "модели",
    "Anthropic.",
    feature,
    "позволяет",
    "решать",
    "задачи",
    "глубже",
    "и",
    "точнее.",
  ];
  const wordsShown = Math.floor(interpolate(frame, [135, 240], [0, responseWords.length], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  }));
  const visibleResponse = responseWords.slice(0, wordsShown).join(" ");

  // Cursor blink (every 15 frames = 0.5s)
  const cursorVisible = Math.floor(frame / 15) % 2 === 0;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bg,
        fontFamily: interTight,
        padding: 60,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {/* Browser mockup */}
      <div
        style={{
          width: 960,
          backgroundColor: colors.card,
          borderRadius: 24,
          border: `2px solid ${colors.border}`,
          overflow: "hidden",
          opacity: browserOpacity,
          transform: `translateY(${browserY}px)`,
          boxShadow: "0 40px 80px rgba(255,87,34,0.15)",
        }}
      >
        {/* Window chrome */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "24px 32px",
            backgroundColor: "#0f0f0f",
            borderBottom: `1px solid ${colors.border}`,
          }}
        >
          <div style={{ width: 16, height: 16, borderRadius: 8, backgroundColor: "#ff5f56" }} />
          <div style={{ width: 16, height: 16, borderRadius: 8, backgroundColor: "#ffbd2e" }} />
          <div style={{ width: 16, height: 16, borderRadius: 8, backgroundColor: "#27c93f" }} />
          <div
            style={{
              flex: 1,
              marginLeft: 24,
              padding: "12px 20px",
              backgroundColor: "#1a1a1a",
              borderRadius: 12,
              color: colors.textDim,
              fontFamily: jetBrainsMono,
              fontSize: 24,
              fontWeight: 400,
            }}
          >
            <span style={{ color: colors.accent }}>https://</span>
            {visibleUrl}
            {urlChars < url.length && cursorVisible && (
              <span style={{ color: colors.accent }}>|</span>
            )}
          </div>
        </div>

        {/* Chat area */}
        <div style={{ padding: 48, minHeight: 720 }}>
          {/* User message bubble */}
          {promptChars > 0 && (
            <div
              style={{
                marginBottom: 32,
                padding: "24px 32px",
                backgroundColor: colors.bg,
                borderRadius: 20,
                borderLeft: `4px solid ${colors.accent}`,
                color: colors.text,
                fontSize: 32,
                fontWeight: 400,
                lineHeight: 1.4,
              }}
            >
              {visiblePrompt}
              {promptChars < prompt.length && cursorVisible && (
                <span style={{ color: colors.accent }}>▊</span>
              )}
            </div>
          )}

          {/* Assistant response */}
          {wordsShown > 0 && (
            <div
              style={{
                padding: "24px 32px",
                backgroundColor: "transparent",
                color: colors.text,
                fontSize: 30,
                fontWeight: 400,
                lineHeight: 1.5,
                opacity: ease(interpolate(frame, [135, 150], [0, 1], { extrapolateRight: "clamp" })),
              }}
            >
              <div
                style={{
                  display: "inline-block",
                  padding: "4px 12px",
                  marginBottom: 12,
                  backgroundColor: colors.accent,
                  color: colors.text,
                  borderRadius: 8,
                  fontSize: 18,
                  fontWeight: 700,
                  letterSpacing: 1.5,
                }}
              >
                CLAUDE
              </div>
              <div>{visibleResponse}</div>
            </div>
          )}
        </div>
      </div>

      {/* Footer brand mark — фикс на низу */}
      <div
        style={{
          position: "absolute",
          bottom: 60,
          left: 0,
          right: 0,
          textAlign: "center",
          color: colors.textDim,
          fontSize: 24,
          fontWeight: 600,
          letterSpacing: 4,
          opacity: browserOpacity,
        }}
      >
        POSTULAT · AI STUDIO
      </div>
    </AbsoluteFill>
  );
};
