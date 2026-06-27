/**
 * WebbGalaxyDemoV2 — переделанная космо-сцена под сценарий Артёма про Webb / XMM-VID1-2075.
 *
 * Самодостаточный full-frame ролик 18 сек, использует:
 *   - storyboard на 9 ударов (вместо 4 длинных сцен в v1) — из design-guide.
 *   - 14 правил дизайна (safe-area 100, hero ≥90, spring везде, breath в начале, 3 цвета максимум).
 *   - design-tokens (COSMIC палитра + SPRING + EASING).
 *
 * Что выкинуто vs v1:
 *   - 4 metadata-карточки → 2 чипа
 *   - Comparison ↻ vs — выкинуто целиком (заменено на vinyl-stop + glitch)
 *   - FROZEN-штамп rotate(-8°) — клише, удалено
 *   - ★ МЫСЛЬ ДНЯ chip — клише, удалено
 *   - 7 цветов → 4 (bgDeep / starGold / textPrimary / galaxyPurple)
 *   - emoji-bullets из 6 spiral arms — заменено на radial gradient + accretion disk
 *
 * 18 сек / 540 frames @ 30fps / 1080×1920 9:16
 *
 * 9 кадров-ударов:
 *   0-18    Кадр 1: одна звезда на чёрном (BREATH)
 *   18-60   Кадр 2: zoom-out → starfield + хедер JAMES WEBB · 2026
 *   60-150  Кадр 3: hero-headline по 3 строкам, ключевые слова золотом
 *   150-180 Кадр 4: галактика появилась
 *   180-300 Кадр 5: галактика медленно вращается + 2 чипа влетают
 *   300-330 Кадр 6: vinyl-stop + camera shake (УДАР №2)
 *   330-420 Кадр 7: «НЕ ВРАЩАЕТСЯ» 140px gradient + sub «Так не должно быть»
 *   420-510 Кадр 8: quote «Учебник — это ЧЕРНОВИК» с glow на ЧЕРНОВИК
 *   510-540 Кадр 9: затемнение + brand-mark POSTULAT · data: NASA / JWST
 */
import {
  AbsoluteFill,
  interpolate,
  random,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  COSMIC,
  SAFE_AREA,
  FONT_SIZE,
  FONT_WEIGHT,
  LETTER_SPACING,
  SPRING,
  EASING,
} from "../design-tokens";
import { interTight, jetBrainsMono } from "../fonts";

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

// Reusable: starfield (used in кадрах 2, 5, 8, 9)
const StarField: React.FC<{ density?: number; opacity?: number }> = ({
  density = 200,
  opacity = 1,
}) => {
  const frame = useCurrentFrame();
  return (
    <>
      {Array.from({ length: density }).map((_, i) => {
        const x = random(`star-x-${i}`) * 1080;
        const y = random(`star-y-${i}`) * 1920;
        const size = 1.5 + random(`star-s-${i}`) * 2;
        const isGold = random(`star-c-${i}`) > 0.85;
        const phase = random(`star-p-${i}`) * Math.PI * 2;
        const twinkle = 0.6 + 0.4 * Math.sin(frame * 0.06 + phase);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: size,
              height: size,
              borderRadius: "50%",
              backgroundColor: isGold ? COSMIC.starGold : COSMIC.starWhite,
              opacity: twinkle * 0.7 * opacity,
              boxShadow: isGold ? `0 0 ${size * 3}px ${COSMIC.starGold}` : "none",
            }}
          />
        );
      })}
    </>
  );
};

// Reusable: галактика как accretion disk + bulge
const Galaxy: React.FC<{
  scale: number;
  rotation: number;
  opacity: number;
}> = ({ scale, rotation, opacity }) => (
  <div
    style={{
      position: "relative",
      width: 720,
      height: 720,
      transform: `scale(${scale}) rotate(${rotation}deg)`,
      opacity,
    }}
  >
    {/* Outer halo */}
    <div
      style={{
        position: "absolute",
        inset: 0,
        borderRadius: "50%",
        background: `radial-gradient(circle, ${COSMIC.galaxyPurple}22 0%, transparent 65%)`,
        filter: "blur(50px)",
      }}
    />
    {/* Accretion disk — мягкий эллипс */}
    <div
      style={{
        position: "absolute",
        inset: "20% 5%",
        borderRadius: "50%",
        background: `radial-gradient(ellipse, ${COSMIC.galaxyPurple}80 0%, ${COSMIC.galaxyBlue}40 40%, transparent 75%)`,
        filter: "blur(8px)",
        transform: "rotate(-15deg)",
      }}
    />
    {/* Core bulge — горячее свечение в центре */}
    <div
      style={{
        position: "absolute",
        inset: "38%",
        borderRadius: "50%",
        background: `radial-gradient(circle, ${COSMIC.starWarm} 0%, ${COSMIC.starGold} 30%, transparent 60%)`,
        boxShadow: `0 0 100px ${COSMIC.starGold}, 0 0 200px ${COSMIC.galaxyPurple}80`,
      }}
    />
    {/* Specks of stars within disk */}
    {Array.from({ length: 60 }).map((_, i) => {
      const angle = random(`gs-${i}`) * Math.PI * 2;
      const radius = 80 + random(`gs-r-${i}`) * 280;
      const x = 360 + Math.cos(angle) * radius;
      const y = 360 + Math.sin(angle) * radius * 0.6; // slight ellipse
      const size = 1.5 + random(`gs-s-${i}`) * 2;
      return (
        <div
          key={i}
          style={{
            position: "absolute",
            left: x,
            top: y,
            width: size,
            height: size,
            borderRadius: "50%",
            backgroundColor: COSMIC.starWhite,
            boxShadow: `0 0 ${size * 2}px ${COSMIC.starWhite}`,
          }}
        />
      );
    })}
  </div>
);

export type WebbGalaxyDemoV2Props = {
  [key: string]: unknown;
};

export const WebbGalaxyDemoV2: React.FC<WebbGalaxyDemoV2Props> = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // КАДР 1 (0-18): одна звезда в центре, BREATH
  // ============================================================================
  const breathStarOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  const breathStarScale = interpolate(frame, [0, 18], [0.4, 1], {
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });

  // ============================================================================
  // КАДР 2 (18-60): zoom-out → starfield + хедер
  // ============================================================================
  const zoomOutScale = interpolate(frame, [18, 60], [8, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });
  const starfieldOpacity = interpolate(frame, [18, 50], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const headerSpring = spring({
    frame: frame - 30,
    fps,
    config: SPRING.snappy,
  });

  // ============================================================================
  // КАДР 3 (60-150): hero headline 3 строки
  // ============================================================================
  const headerExitOpacity = interpolate(frame, [55, 70], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const line1Spring = spring({ frame: frame - 60, fps, config: SPRING.heavy });
  const line2Spring = spring({ frame: frame - 78, fps, config: SPRING.heavy });
  const line3Spring = spring({ frame: frame - 96, fps, config: SPRING.heavy });
  const headlineExit = interpolate(frame, [142, 152], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДРЫ 4-6 (150-330): галактика, чипы, vinyl-stop
  // ============================================================================
  const galaxyEnter = spring({
    frame: frame - 150,
    fps,
    config: SPRING.heavy,
  });
  // Rotation: 0→12° за frames 180-270, then freeze at frame 300 (vinyl stop)
  let galaxyRotation = 0;
  if (frame >= 180 && frame < 300) {
    galaxyRotation = interpolate(frame, [180, 300], [0, 14], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASING.outCubic,
    });
  } else if (frame >= 300) {
    galaxyRotation = 14; // frozen
  }
  // Camera shake on frames 300-318
  const shakeMagnitude = frame >= 300 && frame <= 318
    ? Math.sin((frame - 300) * 1.2) * 6 * (1 - (frame - 300) / 18)
    : 0;
  // White flash on frame 300-303
  const flashOpacity = interpolate(frame, [300, 303, 308], [0, 0.5, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Chips: XMM-VID1-2075 слева (180-300), < 2 млрд лет справа (210-300)
  const chip1Spring = spring({ frame: frame - 180, fps, config: SPRING.snappy });
  const chip2Spring = spring({ frame: frame - 210, fps, config: SPRING.snappy });
  const chipsExit = interpolate(frame, [320, 332], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const galaxyExitScale = interpolate(frame, [320, 340], [1, 0.5], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  const galaxyExitOpacity = interpolate(frame, [320, 340], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 7 (330-420): «НЕ ВРАЩАЕТСЯ» крупно + glitch
  // ============================================================================
  const notRotatingSpring = spring({
    frame: frame - 333,
    fps,
    config: SPRING.heavy,
  });
  // RGB glitch на frames 333-348 (subtle)
  const glitchAmount = frame >= 333 && frame <= 348
    ? (1 - (frame - 333) / 15) * 6
    : 0;
  const subSpring = spring({ frame: frame - 360, fps, config: SPRING.snappy });
  const stage7Exit = interpolate(frame, [410, 422], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 8 (420-510): quote «Учебник — это ЧЕРНОВИК»
  // ============================================================================
  const quoteEnter = interpolate(frame, [420, 445], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  const quoteY = interpolate(frame, [420, 445], [20, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });
  // Glow ramp на ЧЕРНОВИК — 30 frames
  const chernovikGlow = interpolate(frame, [445, 475], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  const stage8Exit = interpolate(frame, [500, 510], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 9 (510-540): brand-mark
  // ============================================================================
  const brandOpacity = interpolate(frame, [510, 525], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COSMIC.bgDeep,
        fontFamily: interTight,
      }}
    >
      {/* Background gradient — постоянный, лёгкий */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at 50% 30%, ${COSMIC.bgLight} 0%, ${COSMIC.bgMid} 40%, ${COSMIC.bgDeep} 100%)`,
          opacity: 0.6,
        }}
      />

      {/* Camera shake wrapper для кадра 6 */}
      <AbsoluteFill
        style={{
          transform: `translate(${shakeMagnitude}px, ${-shakeMagnitude * 0.5}px)`,
        }}
      >
        {/* КАДР 1: BREATH — одна звезда в центре (frames 0-18) */}
        {frame < 60 && (
          <AbsoluteFill
            style={{
              alignItems: "center",
              justifyContent: "center",
              transform: `scale(${frame < 18 ? 1 : zoomOutScale})`,
            }}
          >
            <div
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                backgroundColor: COSMIC.starGold,
                boxShadow: `0 0 30px ${COSMIC.starGold}, 0 0 80px ${COSMIC.starWarm}`,
                opacity: breathStarOpacity,
                transform: `scale(${breathStarScale})`,
              }}
            />
          </AbsoluteFill>
        )}

        {/* КАДРЫ 2-9: starfield (после zoom-out) */}
        {frame >= 18 && (
          <AbsoluteFill style={{ opacity: starfieldOpacity }}>
            <StarField
              density={200}
              opacity={
                frame >= 420 && frame < 510
                  ? 0.3 // dim под quote
                  : 1
              }
            />
          </AbsoluteFill>
        )}

        {/* КАДР 2: хедер JAMES WEBB (40-60, exit 55-70) */}
        {frame >= 40 && frame < 70 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "flex-start",
              alignItems: "center",
            }}
          >
            <div
              style={{
                opacity: headerSpring * headerExitOpacity,
                transform: `translateY(${(1 - headerSpring) * -20}px)`,
                marginTop: 80,
                padding: "12px 24px",
                border: `1px solid ${COSMIC.galaxyPurple}`,
                borderRadius: 100,
                backgroundColor: "rgba(157,78,221,0.1)",
                color: COSMIC.textSecondary,
                fontSize: 28,
                fontWeight: FONT_WEIGHT.semibold,
                letterSpacing: LETTER_SPACING.capsWide,
                fontFamily: jetBrainsMono,
              }}
            >
              🔭 JAMES WEBB · 2026
            </div>
          </AbsoluteFill>
        )}

        {/* КАДР 3: hero headline 3 строки (60-150) */}
        {frame >= 60 && frame < 152 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "center",
              alignItems: "center",
              opacity: headlineExit,
            }}
          >
            <div
              style={{
                color: COSMIC.text,
                fontFamily: interTight,
                textAlign: "center",
                lineHeight: 1.05,
                letterSpacing: LETTER_SPACING.hero,
              }}
            >
              <div
                style={{
                  fontSize: 96,
                  fontWeight: FONT_WEIGHT.bold,
                  opacity: line1Spring,
                  transform: `translateY(${(1 - line1Spring) * 30}px)`,
                  marginBottom: 12,
                }}
              >
                Webb нашёл
              </div>
              <div
                style={{
                  fontSize: 96,
                  fontWeight: FONT_WEIGHT.bold,
                  opacity: line2Spring,
                  transform: `translateY(${(1 - line2Spring) * 30}px)`,
                  marginBottom: 30,
                }}
              >
                галактику
              </div>
              <div
                style={{
                  fontSize: 130,
                  fontWeight: FONT_WEIGHT.black,
                  opacity: line3Spring,
                  transform: `scale(${0.7 + line3Spring * 0.3})`,
                  color: COSMIC.starGold,
                  textShadow: `0 0 40px ${COSMIC.starGold}80`,
                }}
              >
                которая
                <br />
                НЕ ВРАЩАЕТСЯ
              </div>
            </div>
          </AbsoluteFill>
        )}

        {/* КАДРЫ 4-6: галактика + чипы (150-340) */}
        {frame >= 150 && frame < 340 && (
          <>
            {/* Галактика */}
            <AbsoluteFill
              style={{
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Galaxy
                scale={galaxyEnter * galaxyExitScale}
                rotation={galaxyRotation}
                opacity={galaxyEnter * galaxyExitOpacity}
              />
            </AbsoluteFill>

            {/* Chip 1: XMM-VID1-2075 слева сверху */}
            {frame >= 180 && (
              <div
                style={{
                  position: "absolute",
                  top: 280,
                  left: SAFE_AREA,
                  padding: "16px 24px",
                  backgroundColor: "rgba(13,18,64,0.85)",
                  border: `1px solid ${COSMIC.galaxyBlue}`,
                  borderRadius: 12,
                  backdropFilter: "blur(8px)",
                  opacity: chip1Spring * chipsExit,
                  transform: `translateX(${(1 - chip1Spring) * -40}px)`,
                }}
              >
                <div
                  style={{
                    color: COSMIC.textDim,
                    fontSize: 16,
                    fontFamily: jetBrainsMono,
                    fontWeight: FONT_WEIGHT.semibold,
                    letterSpacing: LETTER_SPACING.caps,
                    marginBottom: 4,
                  }}
                >
                  ИМЯ
                </div>
                <div
                  style={{
                    color: COSMIC.text,
                    fontSize: 28,
                    fontFamily: jetBrainsMono,
                    fontWeight: FONT_WEIGHT.bold,
                  }}
                >
                  XMM-VID1-2075
                </div>
              </div>
            )}

            {/* Chip 2: Возраст справа снизу */}
            {frame >= 210 && (
              <div
                style={{
                  position: "absolute",
                  bottom: 280,
                  right: SAFE_AREA,
                  padding: "16px 24px",
                  backgroundColor: "rgba(13,18,64,0.85)",
                  border: `1px solid ${COSMIC.galaxyBlue}`,
                  borderRadius: 12,
                  backdropFilter: "blur(8px)",
                  opacity: chip2Spring * chipsExit,
                  transform: `translateX(${(1 - chip2Spring) * 40}px)`,
                }}
              >
                <div
                  style={{
                    color: COSMIC.textDim,
                    fontSize: 16,
                    fontFamily: jetBrainsMono,
                    fontWeight: FONT_WEIGHT.semibold,
                    letterSpacing: LETTER_SPACING.caps,
                    marginBottom: 4,
                    textAlign: "right",
                  }}
                >
                  ВОЗРАСТ ВСЕЛЕННОЙ
                </div>
                <div
                  style={{
                    color: COSMIC.starGold,
                    fontSize: 28,
                    fontFamily: interTight,
                    fontWeight: FONT_WEIGHT.bold,
                    textAlign: "right",
                  }}
                >
                  &lt; 2 млрд лет
                </div>
              </div>
            )}

            {/* White flash */}
            {flashOpacity > 0 && (
              <AbsoluteFill
                style={{
                  backgroundColor: "#ffffff",
                  opacity: flashOpacity,
                }}
              />
            )}
          </>
        )}

        {/* КАДР 7: «НЕ ВРАЩАЕТСЯ» крупно + sub (330-422) */}
        {frame >= 330 && frame < 425 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "center",
              alignItems: "center",
              opacity: stage7Exit,
            }}
          >
            <div
              style={{
                position: "relative",
                fontSize: 140,
                fontFamily: interTight,
                fontWeight: FONT_WEIGHT.black,
                color: COSMIC.starGold,
                textAlign: "center",
                lineHeight: 1.05,
                letterSpacing: LETTER_SPACING.hero,
                transform: `scale(${0.7 + notRotatingSpring * 0.3})`,
                textShadow: `0 0 60px ${COSMIC.starGold}80, 0 0 120px ${COSMIC.galaxyPurple}40`,
              }}
            >
              {/* RGB-glitch effect — 2 layers offset */}
              {glitchAmount > 0 && (
                <>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      color: "#ff3366",
                      transform: `translate(${glitchAmount}px, 0)`,
                      mixBlendMode: "screen",
                      opacity: 0.8,
                    }}
                  >
                    НЕ ВРАЩАЕТСЯ
                  </div>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      color: "#33ddff",
                      transform: `translate(${-glitchAmount}px, 0)`,
                      mixBlendMode: "screen",
                      opacity: 0.8,
                    }}
                  >
                    НЕ ВРАЩАЕТСЯ
                  </div>
                </>
              )}
              НЕ ВРАЩАЕТСЯ
            </div>

            {/* Sub */}
            <div
              style={{
                marginTop: 40,
                color: COSMIC.textSecondary,
                fontSize: 32,
                fontFamily: interTight,
                fontWeight: FONT_WEIGHT.regular,
                textAlign: "center",
                opacity: subSpring,
                transform: `translateY(${(1 - subSpring) * 20}px)`,
                letterSpacing: 0.5,
              }}
            >
              Так не должно быть.
              <br />
              <span style={{ color: COSMIC.textDim, fontSize: 26 }}>
                Webb · 2026
              </span>
            </div>
          </AbsoluteFill>
        )}

        {/* КАДР 8: quote «Учебник — это ЧЕРНОВИК» (420-510) */}
        {frame >= 420 && frame < 512 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "center",
              alignItems: "center",
              opacity: stage8Exit,
            }}
          >
            <div
              style={{
                color: COSMIC.text,
                fontFamily: interTight,
                textAlign: "center",
                opacity: quoteEnter,
                transform: `translateY(${quoteY}px)`,
              }}
            >
              <div
                style={{
                  fontSize: 80,
                  fontWeight: FONT_WEIGHT.bold,
                  lineHeight: 1.15,
                  letterSpacing: LETTER_SPACING.hero,
                  marginBottom: 20,
                }}
              >
                Учебник —
                <br />
                это
              </div>
              <div
                style={{
                  fontSize: 140,
                  fontWeight: FONT_WEIGHT.black,
                  color: COSMIC.starGold,
                  letterSpacing: 2,
                  textShadow: `0 0 ${chernovikGlow * 60}px ${COSMIC.starGold}, 0 0 ${chernovikGlow * 120}px ${COSMIC.starGold}80`,
                }}
              >
                ЧЕРНОВИК
              </div>
              <div
                style={{
                  marginTop: 30,
                  fontSize: 30,
                  color: COSMIC.textDim,
                  fontFamily: interTight,
                  fontStyle: "italic",
                  fontWeight: FONT_WEIGHT.regular,
                }}
              >
                а не финальная версия
              </div>
            </div>
          </AbsoluteFill>
        )}

        {/* КАДР 9: brand-mark POSTULAT (510-540) */}
        {frame >= 510 && (
          <AbsoluteFill
            style={{
              justifyContent: "flex-end",
              alignItems: "center",
              paddingBottom: 80,
            }}
          >
            <div
              style={{
                opacity: brandOpacity,
                textAlign: "center",
              }}
            >
              <div
                style={{
                  color: COSMIC.textSecondary,
                  fontSize: 22,
                  fontFamily: interTight,
                  fontWeight: FONT_WEIGHT.semibold,
                  letterSpacing: LETTER_SPACING.capsWide,
                  marginBottom: 8,
                }}
              >
                POSTULAT · AI STUDIO
              </div>
              <div
                style={{
                  color: COSMIC.textDim,
                  fontSize: 16,
                  fontFamily: jetBrainsMono,
                  opacity: 0.6,
                }}
              >
                data: NASA / James Webb Space Telescope
              </div>
            </div>
          </AbsoluteFill>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
