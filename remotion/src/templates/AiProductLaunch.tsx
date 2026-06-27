/**
 * AiProductLaunch — параметризуемый шаблон #1 для @panferovai_contentbot.
 *
 * Тип сценария: анонс нового AI-релиза (модель / продукт / семейство).
 * Применение: ChatGPT updates, Claude releases, code-tools новости, AI-стартапы.
 *
 * Формат: 1080×960 (верхняя половина 9:16 split с talking-head внизу)
 * Длительность: 12 сек (360 frames @ 30fps)
 *
 * Структура (4 сцены):
 *   0-2с    Company badge + дата
 *   2-4.5с  Product hero + tagline + audio waveform
 *   4.5-9с  3 model cards последовательно (или 0-3, конфигурируется)
 *   9-12с   Terminal API call + LIVE status badge
 *
 * Props:
 *   - company: компания (e.g. "OpenAI", "Anthropic", "Google DeepMind")
 *   - dateTag: бейдж с датой/типом (e.g. "★ NEW · 2026", "★ BETA · OCT 2026")
 *   - flagshipLabel: лейбл секции 2 (e.g. "FLAGSHIP", "GA RELEASE")
 *   - productName: главное слово продукта (e.g. "GPT-Realtime-2") — часть accent выделяется
 *   - productAccentSplit: с какого индекса начинается accent (для GPT-Realtime-2: split=4 → "GPT-" обычный, "Realtime-2" — accent)
 *   - tagline: подзаголовок (e.g. "Голос на уровне GPT-5")
 *   - taglineHighlight: какое слово в tagline выделить gold-цветом (e.g. "GPT-5")
 *   - showWaveform: показывать waveform (для voice/audio продуктов)
 *   - familyLabel: лейбл секции 3 (e.g. "FAMILY · 3 МОДЕЛИ", "ЧТО ВНУТРИ")
 *   - models: массив 0-3 моделей с emoji, name, tagline (если 0 — секция skip)
 *   - terminalPath: путь в терминале (e.g. "~/openai-realtime")
 *   - terminalCommand: команда после $ (e.g. "curl https://api.openai.com/v1/realtime")
 *   - terminalJson: JSON-payload (e.g. '{"model": "gpt-realtime-2"}')
 *   - liveStatus: статус-бейдж (e.g. "LIVE IN API", "BETA WAITLIST", "GA RELEASED")
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

// ============================================================================
// AUDIO WAVEFORM (опциональный — для voice/audio продуктов)
// ============================================================================
const AudioWaveform: React.FC<{ frame: number; bars?: number; height?: number }> = ({
  frame,
  bars = 36,
  height = 100,
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

// ============================================================================
// MODEL CARD
// ============================================================================
export type ModelSpec = {
  emoji: string;
  name: string;
  tagline: string;
  hot?: boolean; // если true — оранжевый border (для flagship); иначе нейтральный
};

const ModelCard: React.FC<{
  spec: ModelSpec;
  enterSpring: number;
}> = ({ spec, enterSpring }) => {
  const borderColor = spec.hot ? POSTULAT.accent : POSTULAT.border;
  return (
    <div
      style={{
        flex: 1,
        padding: "28px 24px",
        backgroundColor: "rgba(20,20,20,0.95)",
        border: `3px solid ${borderColor}`,
        borderRadius: 18,
        boxShadow: spec.hot
          ? `0 16px 40px rgba(0,0,0,0.65), 0 0 32px ${POSTULAT.accent}40`
          : `0 16px 40px rgba(0,0,0,0.65)`,
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
};

// ============================================================================
// MAIN PROPS
// ============================================================================
export type AiProductLaunchProps = {
  company: string;
  dateTag: string;
  flagshipLabel: string;
  productName: string;
  productAccentSplit: number;
  tagline: string;
  taglineHighlight: string;
  showWaveform: boolean;
  familyLabel: string;
  models: ModelSpec[];
  terminalPath: string;
  terminalCommand: string;
  terminalJson: string;
  liveStatus: string;
  // index signature для Remotion <Composition>
  [key: string]: unknown;
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================
export const AiProductLaunch: React.FC<AiProductLaunchProps> = ({
  // P1 fix #4: defaults для всех props — бот может прислать неполный payload
  company = "Company",
  dateTag = "★ NEW · 2026",
  flagshipLabel = "RELEASE",
  productName = "Product",
  productAccentSplit = 0,
  tagline = "",
  taglineHighlight = "",
  showWaveform = false,
  familyLabel = "ЧТО НОВОГО",
  models = [],
  terminalPath = "~/api",
  terminalCommand = "https://api.example.com/v1/endpoint",
  terminalJson = "{}",
  liveStatus = "LIVE",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // SCENE 1 (0-70): Company badge + date
  // ============================================================================
  const badgeSpring = spring({ frame, fps, config: SPRING.heavy });
  const badgeExit = interpolate(frame, [50, 65], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const dateSpring = spring({ frame: frame - 12, fps, config: SPRING.snappy });

  // ============================================================================
  // SCENE 2 (60-145 если есть models, 60-265 если нет): Hero + tagline + waveform
  // P0 fix #1: при пустом models[] продлеваем Scene 2 до frame 260 чтобы не было дыры
  // ============================================================================
  const heroSpring = spring({ frame: frame - 60, fps, config: SPRING.heavy });
  const hasModels = models.length > 0;
  const heroExitStart = hasModels ? 125 : 245;
  const heroExitEnd = hasModels ? 140 : 260;
  const heroEndFrame = hasModels ? 145 : 265;
  const heroExit = interpolate(frame, [heroExitStart, heroExitEnd], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const waveformOpacity = interpolate(frame, [78, 100], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });

  // Split product name into normal + accent parts
  const productNormal = productName.slice(0, productAccentSplit);
  const productAccent = productName.slice(productAccentSplit);

  // Highlight в tagline — split на before / highlight / after
  const taglineParts = (() => {
    if (!taglineHighlight || !tagline.includes(taglineHighlight)) {
      return { before: tagline, highlight: "", after: "" };
    }
    const idx = tagline.indexOf(taglineHighlight);
    return {
      before: tagline.slice(0, idx),
      highlight: taglineHighlight,
      after: tagline.slice(idx + taglineHighlight.length),
    };
  })();

  // ============================================================================
  // SCENE 3 (135-275): Model cards (если models пустой — Scene 2 заменяет это окно)
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
  // hasModels уже определён в Scene 2

  // ============================================================================
  // SCENE 4 (265-360): Terminal + live badge
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

  const line1Text = `$ curl ${terminalCommand}`;
  const line1Chars = Math.floor(
    interpolate(frame, [280, 303], [0, line1Text.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  // P1 fix #3: адаптивная длительность typewriter в зависимости от длины JSON
  // Базово 15 frames (для ~30-50 char), но не больше 40 frames для длинных JSON
  const line2EndFrame = 305 + Math.min(40, Math.max(12, Math.ceil(terminalJson.length * 0.4)));
  const line2Chars = Math.floor(
    interpolate(frame, [305, line2EndFrame], [0, terminalJson.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  const liveOpacity = interpolate(frame, [320, 333], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const livePulse = frame >= 320 ? 0.7 + 0.3 * Math.sin(frame * 0.4) : 0;
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
          backgroundImage: `linear-gradient(${POSTULAT.accent}0a 1px, transparent 1px), linear-gradient(90deg, ${POSTULAT.accent}0a 1px, transparent 1px)`,
          backgroundSize: "40px 40px",
          opacity: 0.5,
        }}
      />

      {/* === SCENE 1: Company badge === */}
      {frame < 70 && (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            padding: SAFE_AREA,
            opacity: badgeExit,
          }}
        >
          {/* Company logo placeholder — universal CSS spiral */}
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
            {company}
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
            {dateTag}
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 2: Product hero (если !hasModels — продлевается до frame 265) === */}
      {frame >= 60 && frame < heroEndFrame && (
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
              fontSize: 22,
              fontFamily: jetBrainsMono,
              fontWeight: FONT_WEIGHT.bold,
              letterSpacing: LETTER_SPACING.capsWide,
              marginBottom: 18,
              opacity: heroSpring,
              transform: `translateY(${(1 - heroSpring) * -10}px)`,
            }}
          >
            ▸ {flagshipLabel}
          </div>
          <div
            style={{
              color: POSTULAT.text,
              fontSize: 108,
              fontWeight: FONT_WEIGHT.black,
              fontFamily: interTight,
              letterSpacing: LETTER_SPACING.hero,
              lineHeight: 1,
              opacity: heroSpring,
              transform: `scale(${0.6 + heroSpring * 0.4})`,
              textAlign: "center",
            }}
          >
            {productNormal}
            <span style={{ color: POSTULAT.accent }}>{productAccent}</span>
          </div>
          <div
            style={{
              marginTop: 24,
              color: POSTULAT.text,
              fontSize: 34,
              fontWeight: FONT_WEIGHT.medium,
              fontFamily: interTight,
              opacity: clamp01((heroSpring - 0.5) * 2),
              textAlign: "center",
            }}
          >
            {taglineParts.before}
            {taglineParts.highlight && (
              <span
                style={{ color: ACCENTS.warm, fontWeight: FONT_WEIGHT.bold }}
              >
                {taglineParts.highlight}
              </span>
            )}
            {taglineParts.after}
          </div>
          {showWaveform && (
            <div
              style={{
                marginTop: 36,
                opacity: waveformOpacity,
              }}
            >
              <AudioWaveform
                frame={frame > 120 ? 120 : frame}
                bars={36}
                height={100}
              />
            </div>
          )}
        </AbsoluteFill>
      )}

      {/* === SCENE 3: Model cards (conditional) === */}
      {hasModels && frame >= 135 && frame < 275 && (
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
            ▸ {familyLabel}
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            {models[0] && (
              <ModelCard spec={models[0]} enterSpring={card1Spring} />
            )}
            {models[1] && (
              <ModelCard spec={models[1]} enterSpring={card2Spring} />
            )}
            {models[2] && (
              <ModelCard spec={models[2]} enterSpring={card3Spring} />
            )}
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 4: Terminal === */}
      {frame >= 265 && (
        <AbsoluteFill
          style={{
            padding: SAFE_AREA,
            justifyContent: "center",
            opacity: termOpacity,
            transform: `translateY(${termY}px)`,
          }}
        >
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
                {terminalPath}
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
              {/* Line 1: $ command */}
              <div style={{ marginBottom: 10 }}>
                <span style={{ color: "#27c93f" }}>$ </span>
                {line1Text.slice(2, line1Chars)}
                {line1Chars < line1Text.length && cursorOn && (
                  <span style={{ color: POSTULAT.accent }}>▊</span>
                )}
              </div>

              {/* Line 2: JSON */}
              {line1Chars >= line1Text.length && (
                <div>
                  <span style={{ color: POSTULAT.textDim }}>  </span>
                  <span style={{ color: ACCENTS.warm }}>
                    {terminalJson.slice(0, line2Chars)}
                  </span>
                  {line2Chars < terminalJson.length && cursorOn && (
                    <span style={{ color: POSTULAT.accent }}>▊</span>
                  )}
                </div>
              )}

              {/* LIVE badge */}
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
                    {liveStatus}
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

// ============================================================================
// EXAMPLE PROPS — для regression тестирования и как референс структуры
// ============================================================================

// Example 1: OpenAI GPT-Realtime-2 (тот же что был в OpenAiRealtimeBroll)
export const EXAMPLE_OPENAI_REALTIME: AiProductLaunchProps = {
  company: "OpenAI",
  dateTag: "★ NEW · 2026",
  flagshipLabel: "FLAGSHIP",
  productName: "GPT-Realtime-2",
  productAccentSplit: 4, // "GPT-" обычный, "Realtime-2" — accent
  tagline: "Голос на уровне GPT-5",
  taglineHighlight: "GPT-5",
  showWaveform: true,
  familyLabel: "FAMILY · 3 МОДЕЛИ",
  models: [
    { emoji: "🎙", name: "Realtime-2", tagline: "Голос на уровне GPT-5", hot: true },
    { emoji: "🌐", name: "Realtime-Translate", tagline: "Перевод в реальном времени" },
    { emoji: "📝", name: "Realtime-Whisper", tagline: "Стриминговая распознавалка" },
  ],
  terminalPath: "~/openai-realtime",
  terminalCommand: "https://api.openai.com/v1/realtime",
  terminalJson: '{ "model": "gpt-realtime-2", "voice": true }',
  liveStatus: "LIVE IN API",
};

// Example 2: Anthropic Claude Opus 4.7 (другая компания, другой продукт)
export const EXAMPLE_CLAUDE_OPUS: AiProductLaunchProps = {
  company: "Anthropic",
  dateTag: "★ GA · 2026",
  flagshipLabel: "FLAGSHIP",
  productName: "Claude Opus 4.7",
  productAccentSplit: 7, // "Claude " обычный, "Opus 4.7" — accent
  tagline: "Лучшая модель для агентов и сложных задач",
  taglineHighlight: "агентов",
  showWaveform: false, // не voice — waveform не нужен
  familyLabel: "ЧТО НОВОГО",
  models: [
    { emoji: "🧠", name: "Extended thinking", tagline: "Размышляет над задачей минутами", hot: true },
    { emoji: "🔧", name: "Computer use", tagline: "Работает с экраном напрямую" },
    { emoji: "📚", name: "200K context", tagline: "Полный кодbase в одном вызове" },
  ],
  terminalPath: "~/anthropic-sdk",
  terminalCommand: "https://api.anthropic.com/v1/messages",
  terminalJson: '{ "model": "claude-opus-4-7", "thinking": true }',
  liveStatus: "GA RELEASED",
};

// Example 3: Cursor 0.50 (без models — просто продукт без семейства)
export const EXAMPLE_CURSOR: AiProductLaunchProps = {
  company: "Cursor",
  dateTag: "★ UPDATE · 2026",
  flagshipLabel: "v0.50",
  productName: "Cursor Composer",
  productAccentSplit: 7, // "Cursor " обычный, "Composer" — accent
  tagline: "Multi-file edits на уровне Devin",
  taglineHighlight: "Devin",
  showWaveform: false,
  familyLabel: "", // не показывается т.к. models пустой
  models: [], // нет семейства — секция skip
  terminalPath: "~/cursor",
  terminalCommand: "https://cursor.com/composer/api/v1/edit",
  terminalJson: '{ "files": ["src/**/*.ts"], "prompt": "..." }',
  liveStatus: "LIVE NOW",
};
