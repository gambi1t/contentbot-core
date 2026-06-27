/**
 * OpenAiRealtimeBroll — B-roll часть split-layout под новость про GPT-Realtime-2.
 *
 * Формат 1080×960 (верхняя половина 9:16) — для склейки с talking-head снизу.
 * Использование в боте: ffmpeg vstack с аватарной 1080×960 частью.
 *
 * Длительность: 12 сек (360 frames @ 30fps)
 *
 * Эстетика — Бурмистров-style: тёмный фон + оранж акцент + JetBrains Mono
 * для технического текста + minimum CSS-абстракции, максимум реальных UI элементов.
 *
 * Storyboard (новый объект каждые 2-3 сек):
 *   0.0-2.0с (frames 0-60)   OpenAI badge появляется + дата
 *   2.0-4.5с (frames 60-135) GPT-Realtime-2 hero + audio waveform пульсирует
 *   4.5-9.0с (frames 135-270) 3 карточки моделей появляются последовательно
 *   9.0-12.0с (frames 270-360) Terminal API call + ENDPOINT
 */
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  POSTULAT,
  ACCENTS,
  SAFE_AREA,
  FONT_WEIGHT,
  LETTER_SPACING,
  SPRING,
  EASING,
} from "../design-tokens";
import { interTight, jetBrainsMono } from "../fonts";

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

// Audio waveform — анимированные bars (для GPT-Realtime "слышит")
// P0 fix: снижена частота с 0.18 до 0.08 (было ~5.4Hz, опасно стробоскопом)
const AudioWaveform: React.FC<{ frame: number; bars?: number; height?: number }> = ({
  frame,
  bars = 32,
  height = 80,
}) => (
  <div
    style={{
      display: "flex",
      gap: 4,
      alignItems: "center",
      height,
    }}
  >
    {Array.from({ length: bars }).map((_, i) => {
      const phase = (i / bars) * Math.PI * 4;
      const wave = Math.sin(frame * 0.08 + phase) * 0.5 + 0.5;
      const wave2 = Math.sin(frame * 0.14 + phase * 2.3) * 0.3 + 0.7;
      const barHeight = (0.2 + wave * wave2 * 0.8) * height;
      const isHot = i % 7 === 3;
      return (
        <div
          key={i}
          style={{
            width: 6,
            height: barHeight,
            borderRadius: 3,
            backgroundColor: isHot ? POSTULAT.accent : POSTULAT.textDim,
            boxShadow: isHot ? `0 0 12px ${POSTULAT.accent}80` : "none",
          }}
        />
      );
    })}
  </div>
);

// Карточка модели
type ModelCardSpec = {
  emoji: string;
  name: string;
  tagline: string;
  hotColor: string;
};

const ModelCard: React.FC<{
  spec: ModelCardSpec;
  enterSpring: number;
}> = ({ spec, enterSpring }) => (
  <div
    style={{
      flex: 1,
      padding: "28px 24px",
      backgroundColor: "rgba(20,20,20,0.95)",
      border: `3px solid ${spec.hotColor}`,
      borderRadius: 18,
      boxShadow: `0 16px 40px rgba(0,0,0,0.65), 0 0 32px ${spec.hotColor}40`,
      opacity: enterSpring,
      transform: `translateY(${(1 - enterSpring) * 30}px) scale(${0.85 + enterSpring * 0.15})`,
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}
  >
    <div style={{ fontSize: 52, lineHeight: 1 }}>{spec.emoji}</div>
    <div
      style={{
        color: POSTULAT.text,
        fontSize: 28,
        fontWeight: FONT_WEIGHT.bold,
        fontFamily: jetBrainsMono,
        lineHeight: 1.1,
        marginTop: 4,
      }}
    >
      {spec.name}
    </div>
    <div
      style={{
        color: POSTULAT.textDim,
        fontSize: 20,
        fontFamily: interTight,
        fontWeight: FONT_WEIGHT.medium,
        lineHeight: 1.25,
      }}
    >
      {spec.tagline}
    </div>
  </div>
);

const MODELS: ModelCardSpec[] = [
  // P0 fix: оставляем hot только на flagship (Realtime-2),
  // остальные — нейтральные border (border + textDim) — соблюдаем правило 3 цвета
  {
    emoji: "🎙",
    name: "Realtime-2",
    tagline: "Голос на уровне GPT-5",
    hotColor: POSTULAT.accent,
  },
  {
    emoji: "🌐",
    name: "Realtime-Translate",
    tagline: "Перевод в реальном времени",
    hotColor: POSTULAT.border,
  },
  {
    emoji: "📝",
    name: "Realtime-Whisper",
    tagline: "Стриминговая распознавалка",
    hotColor: POSTULAT.border,
  },
];

export type OpenAiRealtimeBrollProps = {
  [key: string]: unknown;
};

export const OpenAiRealtimeBroll: React.FC<OpenAiRealtimeBrollProps> = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // SCENE 1 (0-60, 0-2с): OpenAI badge + дата
  // ============================================================================
  const badgeSpring = spring({ frame, fps, config: SPRING.heavy });
  const badgeExit = interpolate(frame, [50, 65], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const dateSpring = spring({ frame: frame - 12, fps, config: SPRING.snappy });

  // ============================================================================
  // SCENE 2 (60-135, 2-4.5с): GPT-Realtime-2 hero + waveform
  // ============================================================================
  const heroSpring = spring({ frame: frame - 60, fps, config: SPRING.heavy });
  const heroExit = interpolate(frame, [125, 140], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const waveformOpacity = interpolate(frame, [78, 100], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });

  // ============================================================================
  // SCENE 3 (135-270, 4.5-9с): 3 карточки моделей последовательно
  // ============================================================================
  const card1Spring = spring({ frame: frame - 140, fps, config: SPRING.heavy });
  const card2Spring = spring({ frame: frame - 165, fps, config: SPRING.heavy });
  const card3Spring = spring({ frame: frame - 190, fps, config: SPRING.heavy });
  const cardsExit = interpolate(frame, [255, 270], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const sectionLabelSpring = spring({
    frame: frame - 135,
    fps,
    config: SPRING.snappy,
  });

  // ============================================================================
  // SCENE 4 (265-360, 8.8-12с): Terminal API call
  // P1 fix: start сдвинут с 270 на 265 для 5-frame overlap c cardsExit (255-270)
  // ============================================================================
  const termOpacity = interpolate(frame, [265, 280], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const termY = interpolate(frame, [265, 285], [20, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });

  // Type out lines progressively
  const line1Text = "$ curl https://api.openai.com/v1/realtime";
  const line1Chars = Math.floor(
    interpolate(frame, [280, 303], [0, line1Text.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  const line2Text = '{ "model": "gpt-realtime-2", "voice": true }';
  const line2Chars = Math.floor(
    interpolate(frame, [305, 320], [0, line2Text.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  // P0 fix: LIVE IN API сдвинут с 335 на 320 — теперь hold = 360-320 = 40 frames > FRAMES.textHoldMin=36
  const liveOpacity = interpolate(frame, [320, 333], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const livePulse = frame >= 320 ? 0.7 + 0.3 * Math.sin(frame * 0.4) : 0;

  // P2 fix: cursor blink с frame/8 (3.75Hz) на frame/15 (2Hz)
  const cursorOn = Math.floor(frame / 15) % 2 === 0;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: POSTULAT.bg,
        fontFamily: interTight,
        overflow: "hidden",
      }}
    >
      {/* Subtle grid background */}
      <AbsoluteFill
        style={{
          backgroundImage: `linear-gradient(rgba(255,87,34,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,87,34,0.04) 1px, transparent 1px)`,
          backgroundSize: "40px 40px",
          opacity: 0.5,
        }}
      />

      {/* === SCENE 1: OpenAI badge === */}
      {frame < 70 && (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            padding: SAFE_AREA,
            opacity: badgeExit,
          }}
        >
          {/* OpenAI logo placeholder — спираль из CSS */}
          <div
            style={{
              width: 140,
              height: 140,
              borderRadius: "50%",
              backgroundColor: "transparent",
              border: `5px solid ${POSTULAT.text}`,
              position: "relative",
              opacity: badgeSpring,
              transform: `scale(${badgeSpring}) rotate(${frame * 0.15}deg)`,
              marginBottom: 30,
            }}
          >
            <div
              style={{
                position: "absolute",
                inset: -5,
                borderRadius: "50%",
                border: `5px solid ${POSTULAT.accent}`,
                clipPath: "polygon(0 0, 50% 0, 50% 100%, 0 100%)",
              }}
            />
          </div>
          <div
            style={{
              color: POSTULAT.text,
              fontSize: 68,
              fontWeight: FONT_WEIGHT.black,
              fontFamily: interTight,
              letterSpacing: LETTER_SPACING.hero,
              opacity: badgeSpring,
              transform: `translateY(${(1 - badgeSpring) * 20}px)`,
            }}
          >
            OpenAI
          </div>
          <div
            style={{
              marginTop: 16,
              color: POSTULAT.accent,
              fontSize: 24,
              fontWeight: FONT_WEIGHT.bold,
              fontFamily: jetBrainsMono,
              letterSpacing: LETTER_SPACING.capsWide,
              opacity: dateSpring,
            }}
          >
            ★ NEW · 2026
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 2: GPT-Realtime-2 hero === */}
      {frame >= 60 && frame < 145 && (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            padding: SAFE_AREA,
            opacity: heroExit,
          }}
        >
          <div
            style={{
              color: POSTULAT.textDim,
              fontSize: 18,
              fontFamily: jetBrainsMono,
              fontWeight: FONT_WEIGHT.bold,
              letterSpacing: LETTER_SPACING.capsWide,
              marginBottom: 14,
              opacity: heroSpring,
              transform: `translateY(${(1 - heroSpring) * -10}px)`,
            }}
          >
            ▸ FLAGSHIP
          </div>
          <div
            style={{
              color: POSTULAT.text,
              fontSize: 84,
              fontWeight: FONT_WEIGHT.black,
              fontFamily: interTight,
              letterSpacing: LETTER_SPACING.hero,
              lineHeight: 1,
              opacity: heroSpring,
              transform: `scale(${0.6 + heroSpring * 0.4})`,
              textAlign: "center",
            }}
          >
            GPT-<span style={{ color: POSTULAT.accent }}>Realtime-2</span>
          </div>
          <div
            style={{
              marginTop: 18,
              color: POSTULAT.text,
              fontSize: 26,
              fontWeight: FONT_WEIGHT.medium,
              fontFamily: interTight,
              opacity: clamp01((heroSpring - 0.5) * 2),
              textAlign: "center",
            }}
          >
            Голос на уровне <span style={{ color: ACCENTS.warm, fontWeight: FONT_WEIGHT.bold }}>GPT-5</span>
          </div>
          {/* Waveform — P0 fix: freeze после frame 120 для снижения movement budget */}
          <div
            style={{
              marginTop: 30,
              opacity: waveformOpacity,
            }}
          >
            <AudioWaveform
              frame={frame > 120 ? 120 : frame}
              bars={36}
              height={80}
            />
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 3: 3 model cards === */}
      {frame >= 135 && frame < 275 && (
        <AbsoluteFill
          style={{
            padding: SAFE_AREA,
            justifyContent: "center",
            opacity: cardsExit,
          }}
        >
          <div
            style={{
              color: POSTULAT.textDim,
              fontSize: 22,
              fontFamily: jetBrainsMono,
              fontWeight: FONT_WEIGHT.bold,
              letterSpacing: LETTER_SPACING.capsWide,
              marginBottom: 28,
              opacity: sectionLabelSpring,
              transform: `translateY(${(1 - sectionLabelSpring) * -10}px)`,
              textAlign: "center",
            }}
          >
            ▸ FAMILY · 3 МОДЕЛИ
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <ModelCard spec={MODELS[0]} enterSpring={card1Spring} />
            <ModelCard spec={MODELS[1]} enterSpring={card2Spring} />
            <ModelCard spec={MODELS[2]} enterSpring={card3Spring} />
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 4: Terminal API call + LIVE NOW === */}
      {frame >= 265 && (
        <AbsoluteFill
          style={{
            padding: SAFE_AREA,
            justifyContent: "center",
            opacity: termOpacity,
            transform: `translateY(${termY}px)`,
          }}
        >
          {/* Terminal window */}
          <div
            style={{
              backgroundColor: "#0d0d0d",
              border: `2px solid ${POSTULAT.border}`,
              borderRadius: 14,
              overflow: "hidden",
              boxShadow: "0 20px 50px rgba(0,0,0,0.6)",
            }}
          >
            {/* Terminal header */}
            <div
              style={{
                padding: "16px 22px",
                backgroundColor: "#1a1a1a",
                display: "flex",
                alignItems: "center",
                gap: 10,
                borderBottom: `1px solid ${POSTULAT.border}`,
              }}
            >
              <div
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 7,
                  backgroundColor: "#ff5f56",
                }}
              />
              <div
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 7,
                  backgroundColor: "#ffbd2e",
                }}
              />
              <div
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 7,
                  backgroundColor: "#27c93f",
                }}
              />
              <div
                style={{
                  marginLeft: 18,
                  color: POSTULAT.textDim,
                  fontSize: 18,
                  fontFamily: jetBrainsMono,
                }}
              >
                ~/openai-realtime
              </div>
            </div>
            {/* Terminal body */}
            <div
              style={{
                padding: "26px 28px",
                fontFamily: jetBrainsMono,
                fontSize: 28,
                lineHeight: 1.5,
                color: POSTULAT.text,
                minHeight: 240,
              }}
            >
              {/* Line 1 — curl */}
              <div style={{ marginBottom: 10 }}>
                <span style={{ color: "#27c93f" }}>$ </span>
                {line1Text.slice(0, line1Chars)}
                {line1Chars < line1Text.length && cursorOn && (
                  <span style={{ color: POSTULAT.accent }}>▊</span>
                )}
              </div>

              {/* Line 2 — JSON */}
              {line1Chars >= line1Text.length && (
                <div>
                  <span style={{ color: POSTULAT.textDim }}>  </span>
                  <span style={{ color: ACCENTS.warm }}>
                    {line2Text.slice(0, line2Chars)}
                  </span>
                  {line2Chars < line2Text.length && cursorOn && (
                    <span style={{ color: POSTULAT.accent }}>▊</span>
                  )}
                </div>
              )}

              {/* LIVE NOW badge — увеличен размер */}
              {frame >= 320 && (
                <div
                  style={{
                    marginTop: 32,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 14,
                    padding: "12px 22px",
                    backgroundColor: "rgba(39,201,63,0.18)",
                    border: `3px solid #27c93f`,
                    borderRadius: 12,
                    opacity: liveOpacity,
                  }}
                >
                  <div
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: 7,
                      backgroundColor: "#27c93f",
                      opacity: livePulse,
                      boxShadow: `0 0 16px #27c93f`,
                    }}
                  />
                  <span
                    style={{
                      color: "#27c93f",
                      fontSize: 24,
                      fontWeight: FONT_WEIGHT.bold,
                      fontFamily: jetBrainsMono,
                      letterSpacing: LETTER_SPACING.capsWide,
                    }}
                  >
                    LIVE IN API
                  </span>
                </div>
              )}
            </div>
          </div>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
