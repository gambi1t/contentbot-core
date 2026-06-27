/**
 * WebbGalaxyDemo — B-roll для космо-новости про James Webb / XMM-VID1-2075.
 *
 * 18 сек / 540 frames @ 30fps / 1080×1920 9:16
 *
 * Эстетика: editorial science (deep navy + cosmic purple + warm gold),
 * НЕ UI-mockup. Звёздное поле с parallax, спиральная галактика, анимация
 * "должна вращаться vs стоит", крупная sci-mag типографика для финала.
 *
 * Хронометраж:
 *   0.0–3.5с (frames 0–105)   Сцена 1: звёздное поле + headline "Webb нашёл"
 *   3.5–9.0с (frames 105–270) Сцена 2: галактика крупно, метаданные XMM-VID1-2075
 *   9.0–14.0с (frames 270–420) Сцена 3: сравнение "должна вращаться vs стоит"
 *   14.0–18.0с (frames 420–540) Сцена 4: punchline "учебник — черновик"
 */
import { AbsoluteFill, interpolate, useCurrentFrame, Sequence, random } from "remotion";
import { interTight, jetBrainsMono } from "../fonts";

export type WebbGalaxyDemoProps = {
  galaxyName?: string;
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

// --- Cosmic palette ---
const cosmic = {
  bgDeep: "#05071a",
  bgMid: "#0d1240",
  bgLight: "#1a1f5c",
  starWhite: "#ffffff",
  starGold: "#ffd700",
  starWarm: "#ffe5b8",
  galaxyPurple: "#9d4edd",
  galaxyPink: "#e0aaff",
  galaxyBlue: "#5e60ce",
  textPrimary: "#ffffff",
  textSecondary: "#c8d3ff",
  textDim: "#7a85b8",
  accent: "#ffd700",
  danger: "#ef476f",
} as const;

// =========================================================================
// Reusable: Star field background with parallax
// =========================================================================
const StarField: React.FC<{ density?: number; seed?: string }> = ({
  density = 120,
  seed = "stars",
}) => {
  const frame = useCurrentFrame();

  return (
    <>
      {/* Far layer — slow drift */}
      {Array.from({ length: density }).map((_, i) => {
        const x = random(`${seed}-far-x-${i}`) * 1080;
        const baseY = random(`${seed}-far-y-${i}`) * 1920;
        const size = 1 + random(`${seed}-far-s-${i}`) * 2;
        const drift = (frame * 0.3) % 1920;
        const y = (baseY + drift) % 1920;
        const opacity = 0.3 + random(`${seed}-far-o-${i}`) * 0.4;
        const twinkle = 0.7 + 0.3 * Math.sin(frame * 0.05 + i);
        return (
          <div
            key={`far-${i}`}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: size,
              height: size,
              backgroundColor: cosmic.starWhite,
              borderRadius: "50%",
              opacity: opacity * twinkle,
            }}
          />
        );
      })}
      {/* Near layer — faster drift, larger, golden tint on some */}
      {Array.from({ length: 30 }).map((_, i) => {
        const x = random(`${seed}-near-x-${i}`) * 1080;
        const baseY = random(`${seed}-near-y-${i}`) * 1920;
        const size = 2 + random(`${seed}-near-s-${i}`) * 3;
        const drift = (frame * 0.8) % 1920;
        const y = (baseY + drift) % 1920;
        const isGold = random(`${seed}-near-c-${i}`) > 0.6;
        return (
          <div
            key={`near-${i}`}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: size,
              height: size,
              backgroundColor: isGold ? cosmic.starGold : cosmic.starWhite,
              borderRadius: "50%",
              boxShadow: `0 0 ${size * 3}px ${isGold ? cosmic.starGold : cosmic.starWhite}`,
              opacity: 0.6,
            }}
          />
        );
      })}
    </>
  );
};

// =========================================================================
// Reusable: Cosmic gradient background
// =========================================================================
const CosmicBg: React.FC = () => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(ellipse at 30% 20%, ${cosmic.bgLight} 0%, ${cosmic.bgMid} 35%, ${cosmic.bgDeep} 100%)`,
    }}
  />
);

// =========================================================================
// PHASE 1: Headline + star field (0-105)
// =========================================================================
const HeadlineScene: React.FC = () => {
  const frame = useCurrentFrame();

  // Telescope-emoji subtitle fades in (0-30)
  const emojiOpacity = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });

  // Title appears word by word (15-75)
  const titleWords = ["Webb", "нашёл", "галактику", "которая", "не", "вращается"];
  const titleProgress = interpolate(frame, [15, 75], [0, titleWords.length], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Subtitle slides in (75-105)
  const subOpacity = interpolate(frame, [75, 105], [0, 1], { extrapolateRight: "clamp" });
  const subY = interpolate(frame, [75, 105], [20, 0], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill>
      <CosmicBg />
      <StarField density={150} seed="phase1" />

      {/* Centered headline block */}
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: 60 }}>
        {/* Top tag */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            opacity: emojiOpacity,
            marginBottom: 40,
            padding: "10px 20px",
            border: `1px solid ${cosmic.galaxyPurple}`,
            borderRadius: 100,
            backgroundColor: "rgba(157,78,221,0.1)",
          }}
        >
          <span style={{ fontSize: 28 }}>🔭</span>
          <span
            style={{
              color: cosmic.textSecondary,
              fontSize: 22,
              fontFamily: interTight,
              fontWeight: 600,
              letterSpacing: 3,
            }}
          >
            JAMES WEBB · 6 МАЯ 2026
          </span>
        </div>

        {/* Title — word by word */}
        <div
          style={{
            color: cosmic.textPrimary,
            fontSize: 86,
            fontWeight: 800,
            fontFamily: interTight,
            lineHeight: 1.05,
            letterSpacing: -1,
            textAlign: "center",
            maxWidth: 960,
          }}
        >
          {titleWords.map((word, i) => {
            const opacity = clamp01(titleProgress - i);
            const y = (1 - opacity) * 30;
            return (
              <span
                key={i}
                style={{
                  display: "inline-block",
                  marginRight: 18,
                  opacity,
                  transform: `translateY(${y}px)`,
                  color: word === "не" ? cosmic.accent : cosmic.textPrimary,
                }}
              >
                {word}
              </span>
            );
          })}
        </div>

        {/* Subtitle */}
        <div
          style={{
            marginTop: 50,
            color: cosmic.textSecondary,
            fontSize: 34,
            fontFamily: interTight,
            fontWeight: 400,
            lineHeight: 1.3,
            textAlign: "center",
            maxWidth: 880,
            opacity: subOpacity,
            transform: `translateY(${subY}px)`,
          }}
        >
          Древняя галактика молчит, хотя по моделям{" "}
          <span style={{ color: cosmic.accent, fontWeight: 700 }}>
            должна раскручиваться
          </span>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// =========================================================================
// Scene 2: Galaxy hero shot with metadata (105-270)
// =========================================================================
const GalaxyScene: React.FC = () => {
  const frame = useCurrentFrame(); // 0..165

  // Galaxy zoom-in (0-30)
  const galaxyScale = interpolate(frame, [0, 30], [0.4, 1.0], {
    extrapolateRight: "clamp",
    easing: (t) => ease(t, 2),
  });
  const galaxyOpacity = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });

  // Slow rotation (0-130) then frozen at 130
  const rotation = frame < 130 ? frame * 0.3 : 130 * 0.3;

  // "FROZEN" overlay appears at 130
  const frozenOpacity = interpolate(frame, [130, 145], [0, 1], { extrapolateRight: "clamp" });
  const frozenScale = interpolate(frame, [130, 145], [1.5, 1], {
    extrapolateRight: "clamp",
    easing: (t) => ease(t, 3),
  });

  // Metadata cards slide in (40-100)
  const meta = [
    { label: "ИМЯ", value: "XMM-VID1-2075", delay: 40 },
    { label: "ВОЗРАСТ ВСЕЛЕННОЙ", value: "< 2 млрд лет", delay: 60 },
    { label: "СТАТУС", value: "Звёзд НЕ создаёт", delay: 80 },
    { label: "ВРАЩЕНИЕ", value: "≈ 0", delay: 100 },
  ];

  return (
    <AbsoluteFill>
      <CosmicBg />
      <StarField density={80} seed="phase2" />

      {/* Galaxy — top half */}
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 180 }}>
        <div
          style={{
            position: "relative",
            width: 700,
            height: 700,
            opacity: galaxyOpacity,
            transform: `scale(${galaxyScale}) rotate(${rotation}deg)`,
          }}
        >
          {/* Outer glow */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              borderRadius: "50%",
              background: `radial-gradient(circle, ${cosmic.galaxyPurple}30 0%, transparent 60%)`,
              filter: "blur(40px)",
            }}
          />
          {/* Spiral arms — multiple rotated rings */}
          {[0, 60, 120, 180, 240, 300].map((deg, i) => (
            <div
              key={i}
              style={{
                position: "absolute",
                inset: "30%",
                borderRadius: "50%",
                border: `2px solid transparent`,
                borderTopColor: cosmic.galaxyPink,
                borderRightColor: cosmic.galaxyBlue,
                opacity: 0.4 - i * 0.04,
                transform: `rotate(${deg}deg) scale(${1 + i * 0.15})`,
              }}
            />
          ))}
          {/* Bright core */}
          <div
            style={{
              position: "absolute",
              inset: "42%",
              borderRadius: "50%",
              backgroundColor: cosmic.starWarm,
              boxShadow: `0 0 80px ${cosmic.starGold}, 0 0 160px ${cosmic.galaxyPink}`,
            }}
          />
          {/* Speckle of stars within */}
          {Array.from({ length: 40 }).map((_, i) => {
            const angle = random(`g-${i}`) * Math.PI * 2;
            const radius = 100 + random(`g-r-${i}`) * 250;
            const x = 350 + Math.cos(angle) * radius;
            const y = 350 + Math.sin(angle) * radius;
            const size = 2 + random(`g-s-${i}`) * 2;
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: x,
                  top: y,
                  width: size,
                  height: size,
                  backgroundColor: cosmic.starWhite,
                  borderRadius: "50%",
                  boxShadow: `0 0 ${size * 2}px ${cosmic.starWhite}`,
                }}
              />
            );
          })}
        </div>

        {/* "FROZEN" stamp — overlays galaxy at 130 */}
        {frame >= 130 && (
          <div
            style={{
              position: "absolute",
              top: 480,
              padding: "16px 32px",
              backgroundColor: "rgba(239,71,111,0.15)",
              border: `3px solid ${cosmic.danger}`,
              borderRadius: 8,
              transform: `scale(${frozenScale}) rotate(-8deg)`,
              opacity: frozenOpacity,
            }}
          >
            <div
              style={{
                color: cosmic.danger,
                fontSize: 36,
                fontWeight: 800,
                fontFamily: interTight,
                letterSpacing: 6,
              }}
            >
              FROZEN
            </div>
          </div>
        )}
      </AbsoluteFill>

      {/* Metadata cards — bottom */}
      <AbsoluteFill style={{ justifyContent: "flex-end", padding: 50, paddingBottom: 80 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {meta.map((m) => {
            const op = clamp01((frame - m.delay) / 12);
            const y = (1 - op) * 20;
            return (
              <div
                key={m.label}
                style={{
                  padding: "16px 24px",
                  backgroundColor: "rgba(13,18,64,0.8)",
                  border: `1px solid ${cosmic.galaxyBlue}`,
                  borderRadius: 12,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  opacity: op,
                  transform: `translateY(${y}px)`,
                  backdropFilter: "blur(8px)",
                }}
              >
                <span
                  style={{
                    color: cosmic.textDim,
                    fontSize: 18,
                    fontFamily: jetBrainsMono,
                    fontWeight: 600,
                    letterSpacing: 1.5,
                  }}
                >
                  {m.label}
                </span>
                <span
                  style={{
                    color: m.label === "ВРАЩЕНИЕ" ? cosmic.accent : cosmic.textPrimary,
                    fontSize: 26,
                    fontFamily: m.label === "ИМЯ" ? jetBrainsMono : interTight,
                    fontWeight: 700,
                  }}
                >
                  {m.value}
                </span>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// =========================================================================
// Scene 3: "Should rotate vs stays still" comparison (270-420)
// =========================================================================
const ComparisonScene: React.FC = () => {
  const frame = useCurrentFrame(); // 0..150

  const headerOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });

  // Left card — model expectation (rotating arrow)
  const leftOp = interpolate(frame, [20, 50], [0, 1], { extrapolateRight: "clamp" });
  const leftRotation = frame * 4; // continuous rotation

  // Right card — reality (frozen)
  const rightOp = interpolate(frame, [70, 100], [0, 1], { extrapolateRight: "clamp" });

  // VS divider
  const vsOp = interpolate(frame, [50, 70], [0, 1], { extrapolateRight: "clamp" });
  const vsScale = interpolate(frame, [50, 70], [0.5, 1], {
    extrapolateRight: "clamp",
    easing: (t) => ease(t, 2),
  });

  // Conclusion text
  const conclOp = interpolate(frame, [110, 140], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill>
      <CosmicBg />
      <StarField density={50} seed="phase3" />

      <AbsoluteFill style={{ padding: 50, paddingTop: 80, justifyContent: "flex-start" }}>
        {/* Section header */}
        <div
          style={{
            color: cosmic.textPrimary,
            fontSize: 44,
            fontWeight: 800,
            fontFamily: interTight,
            textAlign: "center",
            marginBottom: 60,
            opacity: headerOpacity,
            lineHeight: 1.15,
          }}
        >
          Что говорят модели<br />
          <span style={{ color: cosmic.galaxyPurple }}>vs</span> что показал Webb
        </div>

        {/* Two columns — model expectation / reality */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 24,
          }}
        >
          {/* Card 1: Model expectation */}
          <div
            style={{
              padding: "32px 28px",
              backgroundColor: "rgba(157,78,221,0.1)",
              border: `2px solid ${cosmic.galaxyPurple}`,
              borderRadius: 20,
              opacity: leftOp,
              transform: `translateY(${(1 - leftOp) * 20}px)`,
            }}
          >
            <div
              style={{
                color: cosmic.galaxyPurple,
                fontSize: 18,
                fontFamily: jetBrainsMono,
                fontWeight: 700,
                letterSpacing: 2,
                marginBottom: 16,
              }}
            >
              ПО ТЕКУЩИМ МОДЕЛЯМ
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 32,
              }}
            >
              {/* Rotating arrow */}
              <div
                style={{
                  width: 130,
                  height: 130,
                  borderRadius: "50%",
                  border: `4px dashed ${cosmic.galaxyPurple}`,
                  borderTopColor: cosmic.accent,
                  transform: `rotate(${leftRotation}deg)`,
                  position: "relative",
                  flexShrink: 0,
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: -10,
                    left: "50%",
                    fontSize: 32,
                    transform: "translateX(-50%)",
                  }}
                >
                  ↻
                </div>
              </div>
              <div
                style={{
                  color: cosmic.textPrimary,
                  fontSize: 30,
                  fontFamily: interTight,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                Молодая галактика<br />
                <span style={{ color: cosmic.accent }}>раскручивается</span> от газа и гравитации
              </div>
            </div>
          </div>

          {/* VS */}
          <div
            style={{
              textAlign: "center",
              color: cosmic.textDim,
              fontSize: 28,
              fontWeight: 800,
              fontFamily: jetBrainsMono,
              letterSpacing: 8,
              opacity: vsOp,
              transform: `scale(${vsScale})`,
            }}
          >
            А НА САМОМ ДЕЛЕ
          </div>

          {/* Card 2: Reality */}
          <div
            style={{
              padding: "32px 28px",
              backgroundColor: "rgba(239,71,111,0.08)",
              border: `2px solid ${cosmic.danger}`,
              borderRadius: 20,
              opacity: rightOp,
              transform: `translateY(${(1 - rightOp) * 20}px)`,
            }}
          >
            <div
              style={{
                color: cosmic.danger,
                fontSize: 18,
                fontFamily: jetBrainsMono,
                fontWeight: 700,
                letterSpacing: 2,
                marginBottom: 16,
              }}
            >
              ДАННЫЕ JAMES WEBB
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 32,
              }}
            >
              {/* Static circle = no rotation */}
              <div
                style={{
                  width: 130,
                  height: 130,
                  borderRadius: "50%",
                  border: `4px solid ${cosmic.danger}`,
                  position: "relative",
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <div
                  style={{
                    fontSize: 56,
                    color: cosmic.danger,
                    fontWeight: 800,
                    lineHeight: 1,
                  }}
                >
                  —
                </div>
              </div>
              <div
                style={{
                  color: cosmic.textPrimary,
                  fontSize: 30,
                  fontFamily: interTight,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                Стоит на месте,<br />
                звёзд больше <span style={{ color: cosmic.danger }}>не делает</span>
              </div>
            </div>
          </div>
        </div>

        {/* Conclusion */}
        <div
          style={{
            marginTop: 40,
            color: cosmic.textSecondary,
            fontSize: 26,
            fontFamily: interTight,
            fontWeight: 400,
            textAlign: "center",
            opacity: conclOp,
            fontStyle: "italic",
          }}
        >
          Астрономам теперь чесать затылок<br />
          над сценарием её рождения
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// =========================================================================
// Scene 4: Punchline (420-540)
// =========================================================================
const PunchlineScene: React.FC = () => {
  const frame = useCurrentFrame(); // 0..120

  // Pre-text "МЫСЛЬ ДНЯ" tag (0-25)
  const tagOp = interpolate(frame, [0, 25], [0, 1], { extrapolateRight: "clamp" });

  // Main quote — words appear one by one (15-90)
  const words = [
    "Космос",
    "снова",
    "напомнил:",
    "учебник —",
    "это",
    "ЧЕРНОВИК",
    "а не",
    "финальная версия",
  ];
  const wordsShown = interpolate(frame, [15, 90], [0, words.length], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Footer signature (90-120)
  const sigOp = interpolate(frame, [90, 110], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill>
      <CosmicBg />
      <StarField density={100} seed="phase4" />

      <AbsoluteFill
        style={{ justifyContent: "center", alignItems: "center", padding: 70 }}
      >
        {/* Tag */}
        <div
          style={{
            opacity: tagOp,
            padding: "8px 18px",
            border: `1px solid ${cosmic.accent}`,
            borderRadius: 100,
            color: cosmic.accent,
            fontSize: 20,
            fontFamily: jetBrainsMono,
            fontWeight: 700,
            letterSpacing: 4,
            marginBottom: 60,
          }}
        >
          ★ МЫСЛЬ ДНЯ
        </div>

        {/* Editorial-magazine style quote */}
        <div
          style={{
            color: cosmic.textPrimary,
            fontSize: 70,
            fontFamily: interTight,
            fontWeight: 700,
            textAlign: "center",
            lineHeight: 1.15,
            letterSpacing: -1,
            maxWidth: 960,
          }}
        >
          {words.map((word, i) => {
            const op = clamp01(wordsShown - i);
            const y = (1 - op) * 20;
            const isHighlight = word === "ЧЕРНОВИК";
            return (
              <span
                key={i}
                style={{
                  display: "inline-block",
                  marginRight: 18,
                  opacity: op,
                  transform: `translateY(${y}px)`,
                  color: isHighlight ? cosmic.accent : cosmic.textPrimary,
                  fontSize: isHighlight ? 84 : 70,
                  fontWeight: isHighlight ? 800 : 700,
                  textShadow: isHighlight
                    ? `0 0 30px ${cosmic.accent}80`
                    : "none",
                }}
              >
                {word}
              </span>
            );
          })}
        </div>

        {/* Footer brand */}
        <div
          style={{
            position: "absolute",
            bottom: 70,
            left: 0,
            right: 0,
            textAlign: "center",
            opacity: sigOp,
          }}
        >
          <div
            style={{
              color: cosmic.textDim,
              fontSize: 20,
              fontFamily: interTight,
              fontWeight: 600,
              letterSpacing: 6,
              marginBottom: 6,
            }}
          >
            POSTULAT · AI STUDIO
          </div>
          <div
            style={{
              color: cosmic.textDim,
              fontSize: 16,
              fontFamily: jetBrainsMono,
              opacity: 0.6,
            }}
          >
            data: NASA / James Webb Space Telescope · 6 May 2026
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// =========================================================================
// MAIN
// =========================================================================
export const WebbGalaxyDemo: React.FC<WebbGalaxyDemoProps> = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: cosmic.bgDeep, fontFamily: interTight }}>
      <Sequence from={0} durationInFrames={105}>
        <HeadlineScene />
      </Sequence>
      <Sequence from={105} durationInFrames={165}>
        <GalaxyScene />
      </Sequence>
      <Sequence from={270} durationInFrames={150}>
        <ComparisonScene />
      </Sequence>
      <Sequence from={420} durationInFrames={120}>
        <PunchlineScene />
      </Sequence>
    </AbsoluteFill>
  );
};
