/**
 * AiToolDeepDive — параметризуемый шаблон #2 для @panferovai_contentbot.
 *
 * Тип сценария: разбор/туториал/use-case AI-инструмента.
 * Применение: «Claude умеет X», «Cursor для дизайнеров», «попробовал Windsurf»,
 *   «как использовать Cline», обзор Bolt/v0/Lovable.
 * Эстетика: Бурмистров-style — брутальный логотип + терминал-prompt + опц. output.
 *
 * Формат: 1080×960 (верхняя половина 9:16 split с talking-head внизу)
 * Длительность: 12 сек (360 frames @ 30fps)
 *
 * Структура (4 сцены):
 *   0-2с    Tag-badge сверху + brand intro (логотип появляется)
 *   2-6с    Большой брутальный логотип + prompt-input typewriter
 *   6-10с   Output lines (печатаются построчно как ответ инструмента) ИЛИ result preview
 *   10-12с  Outro: tool name + URL/CTA
 *
 * Props:
 *   - toolName: имя инструмента (e.g. "CLAUDE CODE", "CURSOR")
 *   - toolNameStyle: "block" | "glitch" | "stencil" | "neon"
 *   - tagBadge: small tag сверху (e.g. "★ DEEP DIVE", "★ TUTORIAL")
 *   - promptPrefix: префикс перед промптом (default ">")
 *   - promptText: текст промпта (e.g. "Build an MVP for my food delivery app")
 *   - outputLines: 0-4 строки output (опционально, имитация ответа инструмента)
 *   - outroLine: финальная строчка (e.g. "claude.ai/code")
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
// BRUTALIST TOOL NAME — большой стилизованный логотип
// ============================================================================
type ToolNameStyle = "block" | "glitch" | "stencil" | "neon";

const BrutalistToolName: React.FC<{
  text: string;
  style: ToolNameStyle;
  scale: number;
  frame: number;
}> = ({ text, style, scale, frame }) => {
  const baseStyle: React.CSSProperties = {
    fontSize: 140,
    fontWeight: FONT_WEIGHT.black,
    fontFamily: interTight,
    letterSpacing: LETTER_SPACING.hero,
    lineHeight: 0.95,
    color: POSTULAT.accent,
    textAlign: "center",
    transform: `scale(${scale})`,
    transformOrigin: "center",
    textTransform: "uppercase",
  };

  // Block — простой огромный Inter Tight Black
  if (style === "block") {
    return (
      <div
        style={{
          ...baseStyle,
          textShadow: `0 8px 0 ${POSTULAT.accentDim}, 0 16px 40px ${POSTULAT.accent}66`,
        }}
      >
        {text}
      </div>
    );
  }

  // Stencil — горизонтальные «разрезы» через linear-gradient mask
  if (style === "stencil") {
    return (
      <div
        style={{
          ...baseStyle,
          backgroundImage: `repeating-linear-gradient(
            0deg,
            ${POSTULAT.accent} 0px,
            ${POSTULAT.accent} 16px,
            transparent 16px,
            transparent 22px
          )`,
          WebkitBackgroundClip: "text",
          backgroundClip: "text",
          color: "transparent",
          WebkitTextStroke: `2px ${POSTULAT.accent}80`,
        }}
      >
        {text}
      </div>
    );
  }

  // Glitch — RGB-split (статичный + лёгкое дрожание)
  if (style === "glitch") {
    const glitchOffset = Math.sin(frame * 0.3) * 4 + 8;
    return (
      <div style={{ position: "relative", textAlign: "center" }}>
        <div
          style={{
            ...baseStyle,
            position: "absolute",
            inset: 0,
            color: "#ff1744",
            transform: `scale(${scale}) translate(${glitchOffset}px, 2px)`,
            mixBlendMode: "screen",
            opacity: 0.85,
          }}
        >
          {text}
        </div>
        <div
          style={{
            ...baseStyle,
            position: "absolute",
            inset: 0,
            color: "#00e5ff",
            transform: `scale(${scale}) translate(${-glitchOffset}px, -2px)`,
            mixBlendMode: "screen",
            opacity: 0.85,
          }}
        >
          {text}
        </div>
        <div style={{ ...baseStyle, color: POSTULAT.text, position: "relative" }}>
          {text}
        </div>
      </div>
    );
  }

  // Neon — glowing edges
  if (style === "neon") {
    return (
      <div
        style={{
          ...baseStyle,
          color: ACCENTS.warm,
          textShadow: `
            0 0 10px ${ACCENTS.warm},
            0 0 20px ${ACCENTS.warm},
            0 0 40px ${POSTULAT.accent},
            0 0 80px ${POSTULAT.accent}80
          `,
        }}
      >
        {text}
      </div>
    );
  }

  return <div style={baseStyle}>{text}</div>;
};

// ============================================================================
// PROPS
// ============================================================================
export type AiToolDeepDiveProps = {
  toolName: string;
  toolNameStyle: ToolNameStyle;
  tagBadge: string;
  promptPrefix: string;
  promptText: string;
  outputLines: string[]; // 0-4 строки
  outroLine: string;
  // index signature
  [key: string]: unknown;
};

// ============================================================================
// MAIN
// ============================================================================
export const AiToolDeepDive: React.FC<AiToolDeepDiveProps> = ({
  toolName = "TOOL",
  toolNameStyle = "block",
  tagBadge = "★ DEEP DIVE",
  promptPrefix = ">",
  promptText = "what should I build?",
  outputLines = [],
  outroLine = "",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // SCENE 1 (0-60, 0-2с): Tag badge + tool name fade-in (small)
  // ============================================================================
  const tagSpring = spring({ frame, fps, config: SPRING.snappy });
  const tagExit = interpolate(frame, [50, 65], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const introNameSpring = spring({ frame: frame - 18, fps, config: SPRING.heavy });

  // ============================================================================
  // SCENE 2 (60-180 / 60-290): Big brutalist name + prompt typewriter
  // P0 fix: при !hasOutput продлеваем Scene 2 до frame 290 чтобы не было дыры
  // ============================================================================
  const heroSpring = spring({ frame: frame - 60, fps, config: SPRING.heavy });
  const hasOutput = outputLines.length > 0;
  const heroExitStart = hasOutput ? 165 : 275;
  const heroExitEnd = hasOutput ? 180 : 290;
  const heroEndFrame = hasOutput ? 180 : 290;
  const heroExit = interpolate(frame, [heroExitStart, heroExitEnd], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Prompt typewriter: 60 frames на печать (start frame 90, end depends on length)
  const promptStartFrame = 90;
  const promptEndFrame = promptStartFrame + Math.min(60, Math.max(20, promptText.length * 1.2));
  const promptChars = Math.floor(
    interpolate(frame, [promptStartFrame, promptEndFrame], [0, promptText.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  // ============================================================================
  // SCENE 3 (180-300, 6-10с): Output lines (если есть) ИЛИ skip → растянуть Scene 2
  // ============================================================================
  // hasOutput уже определён в Scene 2
  const outputStartFrame = 180;
  const outputContainerOpacity = interpolate(
    frame,
    [outputStartFrame, outputStartFrame + 12],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASING.cinematic },
  );
  const outputContainerY = interpolate(
    frame,
    [outputStartFrame, outputStartFrame + 18],
    [20, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASING.cinematic },
  );

  // P1 fix: lineGap 25→18 чтобы 4-я строка успела допечататься до outputExit
  const linePerCharFrames = 1.2;
  const lineGap = 18;
  const computeLineProgress = (lineIdx: number) => {
    const lineStart = outputStartFrame + 18 + lineIdx * lineGap;
    const lineEnd = lineStart + (outputLines[lineIdx]?.length ?? 0) * linePerCharFrames;
    if (frame < lineStart) return 0;
    return Math.min(1, (frame - lineStart) / Math.max(1, lineEnd - lineStart));
  };

  const outputExit = interpolate(frame, [285, 300], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Cursor blink
  const cursorOn = Math.floor(frame / 15) % 2 === 0;

  // ============================================================================
  // SCENE 4 (290-360, ~10-12с): Outro — tool name + URL
  // ============================================================================
  const outroSpring = spring({ frame: frame - 290, fps, config: SPRING.heavy });
  const outroNameScale = 0.6 + outroSpring * 0.4;
  const outroLineSpring = spring({ frame: frame - 310, fps, config: SPRING.snappy });

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

      {/* === SCENE 1: tag + small tool intro === */}
      {frame < 70 && (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            padding: SAFE_AREA,
            opacity: tagExit,
          }}
        >
          {/* Tag badge */}
          <div
            style={{
              opacity: tagSpring,
              transform: `translateY(${(1 - tagSpring) * -20}px)`,
              padding: "12px 24px",
              border: `2px solid ${POSTULAT.accent}`,
              borderRadius: 100,
              backgroundColor: POSTULAT.accent + "1f",
              color: POSTULAT.text,
              fontSize: 24,
              fontWeight: FONT_WEIGHT.bold,
              letterSpacing: LETTER_SPACING.capsWide,
              fontFamily: jetBrainsMono,
              marginBottom: 36,
            }}
          >
            {tagBadge}
          </div>
          {/* Small intro name — preview of the bigger one */}
          <div
            style={{
              opacity: introNameSpring,
              transform: `scale(${0.7 + introNameSpring * 0.3})`,
              color: POSTULAT.text,
              fontSize: 56,
              fontWeight: FONT_WEIGHT.black,
              fontFamily: interTight,
              letterSpacing: LETTER_SPACING.hero,
              textTransform: "uppercase",
            }}
          >
            {toolName}
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 2: HERO brutalist name + prompt (продлевается до 290 если нет output) === */}
      {frame >= 60 && frame < heroEndFrame && (
        <AbsoluteFill
          style={{
            padding: SAFE_AREA,
            justifyContent: "center",
            alignItems: "center",
            opacity: heroExit,
            gap: 40,
          }}
        >
          <BrutalistToolName
            text={toolName}
            style={toolNameStyle}
            scale={0.7 + heroSpring * 0.3}
            frame={frame}
          />
          {/* Prompt input — terminal-style box */}
          <div
            style={{
              width: "100%",
              maxWidth: 880,
              padding: "20px 26px",
              backgroundColor: "#0d0d0d",
              border: `2px solid ${POSTULAT.border}`,
              borderRadius: 14,
              fontFamily: jetBrainsMono,
              fontSize: 26,
              color: POSTULAT.text,
              opacity: clamp01((heroSpring - 0.4) * 2),
              boxShadow: `0 12px 36px rgba(0,0,0,0.5)`,
              minHeight: 80,
              display: "flex",
              alignItems: "center",
            }}
          >
            <span style={{ color: POSTULAT.accent, marginRight: 10 }}>
              {promptPrefix}
            </span>
            <span>{promptText.slice(0, promptChars)}</span>
            {promptChars < promptText.length && cursorOn && (
              <span style={{ color: POSTULAT.accent, marginLeft: 2 }}>▊</span>
            )}
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 3: output lines (если есть) === */}
      {hasOutput && frame >= outputStartFrame && frame < 305 && (
        <AbsoluteFill
          style={{
            padding: SAFE_AREA,
            justifyContent: "center",
            opacity: outputExit,
          }}
        >
          <div
            style={{
              backgroundColor: "#0d0d0d",
              border: `2px solid ${POSTULAT.border}`,
              borderRadius: 14,
              padding: "26px 30px",
              fontFamily: jetBrainsMono,
              fontSize: 22,
              lineHeight: 1.5,
              color: POSTULAT.text,
              opacity: outputContainerOpacity,
              transform: `translateY(${outputContainerY}px)`,
              boxShadow: `0 16px 40px rgba(0,0,0,0.6), 0 0 24px ${POSTULAT.accent}30`,
              minHeight: 240,
            }}
          >
            {/* Header: показываем что это «output» */}
            <div
              style={{
                color: POSTULAT.textDim,
                fontSize: 14,
                letterSpacing: LETTER_SPACING.capsWide,
                fontWeight: FONT_WEIGHT.bold,
                marginBottom: 18,
              }}>
              ▸ OUTPUT
            </div>
            {/* Lines */}
            {outputLines.map((line, idx) => {
              const progress = computeLineProgress(idx);
              if (progress === 0) return null;
              const charsShown = Math.floor(progress * line.length);
              const isCurrentLine = progress < 1;
              // Markup: ✓ → green, ✗ → red, $ → green prompt, rest → white
              let lineColor: string = POSTULAT.text;
              if (line.startsWith("✓")) lineColor = "#27c93f";
              else if (line.startsWith("✗")) lineColor = "#ff5f56";
              else if (line.startsWith("$")) lineColor = "#27c93f";
              return (
                <div key={idx} style={{ marginBottom: 8, color: lineColor }}>
                  {line.slice(0, charsShown)}
                  {isCurrentLine && cursorOn && (
                    <span style={{ color: POSTULAT.accent, marginLeft: 2 }}>▊</span>
                  )}
                </div>
              );
            })}
          </div>
        </AbsoluteFill>
      )}

      {/* === SCENE 4: outro tool name + url === */}
      {frame >= 290 && (
        <AbsoluteFill
          style={{
            padding: SAFE_AREA,
            justifyContent: "center",
            alignItems: "center",
            gap: 24,
          }}
        >
          <BrutalistToolName
            text={toolName}
            style={toolNameStyle}
            scale={outroNameScale * 0.7}
            frame={frame}
          />
          {outroLine && (
            <div
              style={{
                opacity: outroLineSpring,
                transform: `translateY(${(1 - outroLineSpring) * 20}px)`,
                color: POSTULAT.text,
                fontSize: 32,
                fontFamily: jetBrainsMono,
                fontWeight: FONT_WEIGHT.bold,
                letterSpacing: LETTER_SPACING.caps,
                padding: "14px 28px",
                border: `2px solid ${POSTULAT.accent}`,
                borderRadius: 12,
                backgroundColor: POSTULAT.accent + "14",
              }}
            >
              {outroLine}
            </div>
          )}
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};

// ============================================================================
// EXAMPLES
// ============================================================================

// Example 1: Claude Code (Бурмистров-style ровно — block + prompt)
export const EXAMPLE_CLAUDE_CODE: AiToolDeepDiveProps = {
  toolName: "CLAUDE CODE",
  toolNameStyle: "block",
  tagBadge: "★ DEEP DIVE",
  promptPrefix: ">",
  promptText: "Build an MVP for my food delivery app",
  outputLines: [
    "✓ created next.js app",
    "✓ added auth + database",
    "✓ wired stripe checkout",
    "$ npm run dev → ready in 2.3s",
  ],
  outroLine: "claude.ai/code",
};

// Example 2: Cursor (glitch стиль)
export const EXAMPLE_CURSOR_TOOL: AiToolDeepDiveProps = {
  toolName: "CURSOR",
  toolNameStyle: "glitch",
  tagBadge: "★ TUTORIAL",
  promptPrefix: "/",
  promptText: "edit all files: add error boundaries",
  outputLines: [
    "✓ scanning 47 files",
    "✓ added boundary in App.tsx",
    "✓ added boundary in 12 routes",
    "✗ skipped 2 (already wrapped)",
  ],
  outroLine: "cursor.com",
};

// Example 3: Lovable (neon стиль, без output — короткий тизер)
export const EXAMPLE_LOVABLE: AiToolDeepDiveProps = {
  toolName: "LOVABLE",
  toolNameStyle: "neon",
  tagBadge: "★ ОБЗОР",
  promptPrefix: ">",
  promptText: "make me a SaaS landing page in 30 seconds",
  outputLines: [], // пустой — Scene 3 пропустится
  outroLine: "lovable.dev",
};
