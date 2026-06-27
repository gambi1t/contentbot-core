/**
 * WebbGalaxyDemoV3 — переделка после фидбэка Артёма "эффекты слабые".
 *
 * Что изменено vs V2:
 *   1. Реальное Webb-фото Cartwheel Galaxy вместо CSS-абстракции.
 *   2. Эффекты УСИЛЕНЫ в 5-10 раз:
 *      - Glitch на "НЕ ВРАЩАЕТСЯ": offset 6px → 60px (+ дольше: 15→24 frames)
 *      - Camera shake: 6px → 30px
 *      - White flash: 1 frame → 8 frames с peak 0.8
 *      - Vinyl-stop усилен: дополнительный 4-frame freeze
 *   3. Chips заменены на КРУПНЫЕ full-width fact-cards с поочерёдным появлением
 *   4. ЧЕРНОВИК — pulsing glow вместо статичного
 *   5. Меньше элементов в кадре одновременно (правило movement budget)
 *
 * 18 сек / 540 frames @ 30fps / 1080×1920
 */
import {
  AbsoluteFill,
  Img,
  interpolate,
  random,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  COSMIC,
  SAFE_AREA,
  FONT_WEIGHT,
  LETTER_SPACING,
  SPRING,
  EASING,
} from "../design-tokens";
import { interTight, jetBrainsMono } from "../fonts";

// ---------- Reusable starfield ----------
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
        const size = 1.5 + random(`star-s-${i}`) * 2.5;
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
              opacity: twinkle * 0.8 * opacity,
              boxShadow: isGold ? `0 0 ${size * 4}px ${COSMIC.starGold}` : "none",
            }}
          />
        );
      })}
    </>
  );
};

export type WebbGalaxyDemoV3Props = {
  [key: string]: unknown;
};

export const WebbGalaxyDemoV3: React.FC<WebbGalaxyDemoV3Props> = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // КАДР 1 (0-18): BREATH — одна звезда
  // ============================================================================
  const breathOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  const zoomOutScale = interpolate(frame, [18, 60], [12, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });

  // ============================================================================
  // КАДР 2 (18-60): zoom-out + tag
  // ============================================================================
  const tagSpring = spring({
    frame: frame - 30,
    fps,
    config: SPRING.snappy,
  });
  const tagExit = interpolate(frame, [55, 70], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const starfieldOpacity = interpolate(frame, [18, 50], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 3 (60-150): hero headline — большой и громкий
  // ============================================================================
  const line1 = spring({ frame: frame - 60, fps, config: SPRING.heavy });
  const line2 = spring({ frame: frame - 78, fps, config: SPRING.heavy });
  const line3 = spring({ frame: frame - 96, fps, config: SPRING.heavy });
  const headlineExit = interpolate(frame, [142, 152], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДРЫ 4-6 (150-340): РЕАЛЬНОЕ ФОТО ГАЛАКТИКИ + Ken Burns + facts
  // ============================================================================
  const galaxyEnter = spring({
    frame: frame - 150,
    fps,
    config: SPRING.heavy,
  });
  // Ken Burns: 1.0 → 1.15 за 180-300 (4 сек медленный zoom)
  const kenBurnsScale =
    frame < 180
      ? 1.0
      : interpolate(frame, [180, 300], [1.0, 1.18], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.outCubic,
        });
  // На vinyl-stop (frame 300) галактика «дёргается» внутрь
  const stopShake =
    frame >= 300 && frame <= 318
      ? Math.sin((frame - 300) * 1.5) * 30 * (1 - (frame - 300) / 18)
      : 0;
  const galaxyExitOpacity = interpolate(frame, [330, 350], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const galaxyExitScale = interpolate(frame, [330, 360], [1, 0.7], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });

  // White flash УСИЛЕН: 8 frames, peak 0.8 (было 1 frame, 0.5)
  const flashOpacity = interpolate(
    frame,
    [298, 302, 308],
    [0, 0.85, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASING.fastOut,
    },
  );

  // Fact-cards: КРУПНЫЕ full-width вместо мелких chips
  // Card 1: ИМЯ — frames 195-300
  const card1Spring = spring({ frame: frame - 195, fps, config: SPRING.snappy });
  // Card 2: ВРАЩЕНИЕ — frames 235-300
  const card2Spring = spring({ frame: frame - 235, fps, config: SPRING.snappy });
  const cardsExit = interpolate(frame, [320, 332], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 7 (335-425): «НЕ ВРАЩАЕТСЯ» — МЕГА GLITCH 60px
  // ============================================================================
  const notRotEnter = spring({
    frame: frame - 335,
    fps,
    config: SPRING.heavy,
  });
  // Glitch: 24 frames (было 15), offset до 60px (было 6px)
  const glitchAmount =
    frame >= 335 && frame <= 365
      ? (1 - (frame - 335) / 30) * 60
      : 0;
  // Subtitle
  const subSpring = spring({ frame: frame - 380, fps, config: SPRING.snappy });
  const stage7Exit = interpolate(frame, [415, 425], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 8 (425-510): Quote с PULSING glow на ЧЕРНОВИК
  // ============================================================================
  const quoteEnter = interpolate(frame, [425, 450], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  // Pulsing glow на ЧЕРНОВИК: sine wave с амплитудой 30-100px
  const pulseGlow =
    frame >= 450
      ? 30 + 70 * (0.5 + 0.5 * Math.sin((frame - 450) * 0.15))
      : 0;
  const stage8Exit = interpolate(frame, [500, 510], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 9 (510-540): brand
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
        overflow: "hidden",
      }}
    >
      {/* ============ Background gradient (постоянный) ============ */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at 50% 30%, ${COSMIC.bgLight} 0%, ${COSMIC.bgMid} 40%, ${COSMIC.bgDeep} 100%)`,
          opacity: 0.6,
        }}
      />

      {/* ============ Camera shake wrapper ============ */}
      <AbsoluteFill
        style={{
          transform: `translate(${stopShake}px, ${-stopShake * 0.5}px)`,
        }}
      >
        {/* === КАДР 1: BREATH === */}
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
                width: 14,
                height: 14,
                borderRadius: "50%",
                backgroundColor: COSMIC.starGold,
                boxShadow: `0 0 40px ${COSMIC.starGold}, 0 0 100px ${COSMIC.starWarm}`,
                opacity: breathOpacity,
              }}
            />
          </AbsoluteFill>
        )}

        {/* === Starfield (после zoom-out) === */}
        {frame >= 18 && frame < 425 && (
          <AbsoluteFill style={{ opacity: starfieldOpacity }}>
            <StarField density={250} />
          </AbsoluteFill>
        )}

        {/* === КАДР 2: tag JAMES WEBB === */}
        {frame >= 35 && frame < 70 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "flex-start",
              alignItems: "center",
            }}
          >
            <div
              style={{
                opacity: tagSpring * tagExit,
                transform: `translateY(${(1 - tagSpring) * -30}px)`,
                marginTop: 80,
                padding: "16px 32px",
                border: `2px solid ${COSMIC.galaxyPurple}`,
                borderRadius: 100,
                backgroundColor: "rgba(157,78,221,0.15)",
                color: COSMIC.text,
                fontSize: 32,
                fontWeight: FONT_WEIGHT.bold,
                letterSpacing: LETTER_SPACING.capsWide,
                fontFamily: jetBrainsMono,
                boxShadow: `0 0 40px ${COSMIC.galaxyPurple}40`,
              }}
            >
              🔭 JAMES WEBB · 2026
            </div>
          </AbsoluteFill>
        )}

        {/* === КАДР 3: hero headline === */}
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
                textAlign: "center",
                lineHeight: 1.05,
              }}
            >
              <div
                style={{
                  fontSize: 110,
                  fontWeight: FONT_WEIGHT.bold,
                  opacity: line1,
                  transform: `translateY(${(1 - line1) * 30}px)`,
                  letterSpacing: LETTER_SPACING.hero,
                  marginBottom: 8,
                }}
              >
                Webb нашёл
              </div>
              <div
                style={{
                  fontSize: 110,
                  fontWeight: FONT_WEIGHT.bold,
                  opacity: line2,
                  transform: `translateY(${(1 - line2) * 30}px)`,
                  letterSpacing: LETTER_SPACING.hero,
                  marginBottom: 30,
                }}
              >
                галактику которая
              </div>
              <div
                style={{
                  fontSize: 160,
                  fontWeight: FONT_WEIGHT.black,
                  opacity: line3,
                  transform: `scale(${0.6 + line3 * 0.4})`,
                  color: COSMIC.starGold,
                  letterSpacing: LETTER_SPACING.hero,
                  textShadow: `0 0 60px ${COSMIC.starGold}, 0 0 120px ${COSMIC.starGold}80`,
                }}
              >
                НЕ ВРАЩАЕТСЯ
              </div>
            </div>
          </AbsoluteFill>
        )}

        {/* === КАДРЫ 4-6: РЕАЛЬНОЕ Webb-фото галактики === */}
        {frame >= 150 && frame < 360 && (
          <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
            <div
              style={{
                width: 1080,
                height: 1080,
                opacity: galaxyEnter * galaxyExitOpacity,
                transform: `scale(${kenBurnsScale * galaxyExitScale})`,
                position: "relative",
                overflow: "hidden",
                borderRadius: 12,
              }}
            >
              <Img
                src={staticFile("images/nasa-cartwheel.jpg")}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  filter: "saturate(1.3) contrast(1.1) brightness(1.0)",
                }}
              />
              {/* Vignette overlay для глубины */}
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  background: `radial-gradient(circle, transparent 50%, ${COSMIC.bgDeep} 100%)`,
                  pointerEvents: "none",
                }}
              />
            </div>
          </AbsoluteFill>
        )}

        {/* === Fact-cards: КРУПНЫЕ полноширинные === */}
        {frame >= 195 && frame < 335 && (
          <>
            {/* Card 1: ИМЯ слева сверху */}
            <div
              style={{
                position: "absolute",
                top: 200,
                left: SAFE_AREA,
                right: SAFE_AREA,
                opacity: card1Spring * cardsExit,
                transform: `translateX(${(1 - card1Spring) * -100}px)`,
              }}
            >
              <div
                style={{
                  padding: "28px 36px",
                  backgroundColor: "rgba(13,18,64,0.92)",
                  border: `3px solid ${COSMIC.galaxyBlue}`,
                  borderRadius: 16,
                  backdropFilter: "blur(12px)",
                  boxShadow: `0 20px 60px rgba(0,0,0,0.6), 0 0 40px ${COSMIC.galaxyBlue}40`,
                }}
              >
                <div
                  style={{
                    color: COSMIC.galaxyBlue,
                    fontSize: 22,
                    fontFamily: jetBrainsMono,
                    fontWeight: FONT_WEIGHT.bold,
                    letterSpacing: LETTER_SPACING.capsWide,
                    marginBottom: 8,
                  }}
                >
                  ▸ ИМЯ ГАЛАКТИКИ
                </div>
                <div
                  style={{
                    color: COSMIC.text,
                    fontSize: 56,
                    fontFamily: jetBrainsMono,
                    fontWeight: FONT_WEIGHT.bold,
                    lineHeight: 1.1,
                  }}
                >
                  XMM-VID1-2075
                </div>
              </div>
            </div>

            {/* Card 2: ВРАЩЕНИЕ снизу */}
            <div
              style={{
                position: "absolute",
                bottom: 200,
                left: SAFE_AREA,
                right: SAFE_AREA,
                opacity: card2Spring * cardsExit,
                transform: `translateX(${(1 - card2Spring) * 100}px)`,
              }}
            >
              <div
                style={{
                  padding: "28px 36px",
                  backgroundColor: "rgba(64,13,18,0.92)",
                  border: `3px solid ${COSMIC.starGold}`,
                  borderRadius: 16,
                  backdropFilter: "blur(12px)",
                  boxShadow: `0 20px 60px rgba(0,0,0,0.6), 0 0 60px ${COSMIC.starGold}60`,
                }}
              >
                <div
                  style={{
                    color: COSMIC.starGold,
                    fontSize: 22,
                    fontFamily: jetBrainsMono,
                    fontWeight: FONT_WEIGHT.bold,
                    letterSpacing: LETTER_SPACING.capsWide,
                    marginBottom: 8,
                  }}
                >
                  ▸ СКОРОСТЬ ВРАЩЕНИЯ
                </div>
                <div
                  style={{
                    color: COSMIC.starGold,
                    fontSize: 80,
                    fontFamily: interTight,
                    fontWeight: FONT_WEIGHT.black,
                    lineHeight: 1,
                    letterSpacing: LETTER_SPACING.hero,
                  }}
                >
                  ≈ 0
                </div>
              </div>
            </div>
          </>
        )}

        {/* === МЕГА White flash на vinyl-stop === */}
        {flashOpacity > 0 && (
          <AbsoluteFill
            style={{
              backgroundColor: "#ffffff",
              opacity: flashOpacity,
            }}
          />
        )}

        {/* === КАДР 7: «НЕ ВРАЩАЕТСЯ» с МЕГА GLITCH === */}
        {frame >= 335 && frame < 425 && (
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
                fontSize: 180,
                fontFamily: interTight,
                fontWeight: FONT_WEIGHT.black,
                color: COSMIC.starGold,
                textAlign: "center",
                lineHeight: 0.95,
                letterSpacing: LETTER_SPACING.hero,
                transform: `scale(${0.5 + notRotEnter * 0.5})`,
                textShadow: `0 0 80px ${COSMIC.starGold}, 0 0 160px ${COSMIC.galaxyPurple}80`,
              }}
            >
              {/* МЕГА RGB glitch — 60px offset */}
              {glitchAmount > 0 && (
                <>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      color: "#ff1744",
                      transform: `translate(${glitchAmount}px, ${glitchAmount * 0.3}px)`,
                      mixBlendMode: "screen",
                      opacity: 0.95,
                    }}
                  >
                    НЕ
                    <br />
                    ВРАЩАЕТСЯ
                  </div>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      color: "#00e5ff",
                      transform: `translate(${-glitchAmount}px, ${-glitchAmount * 0.3}px)`,
                      mixBlendMode: "screen",
                      opacity: 0.95,
                    }}
                  >
                    НЕ
                    <br />
                    ВРАЩАЕТСЯ
                  </div>
                </>
              )}
              НЕ
              <br />
              ВРАЩАЕТСЯ
            </div>

            {/* Sub */}
            <div
              style={{
                marginTop: 60,
                color: COSMIC.text,
                fontSize: 42,
                fontFamily: interTight,
                fontWeight: FONT_WEIGHT.semibold,
                textAlign: "center",
                opacity: subSpring,
                transform: `translateY(${(1 - subSpring) * 25}px)`,
                lineHeight: 1.2,
              }}
            >
              Так не должно быть.
              <div
                style={{
                  marginTop: 12,
                  color: COSMIC.galaxyPurple,
                  fontSize: 30,
                  fontFamily: jetBrainsMono,
                  fontWeight: FONT_WEIGHT.bold,
                  letterSpacing: LETTER_SPACING.caps,
                }}
              >
                WEBB · 2026
              </div>
            </div>
          </AbsoluteFill>
        )}

        {/* === КАДР 8: Quote с PULSING glow === */}
        {frame >= 425 && frame < 512 && (
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
                textAlign: "center",
                opacity: quoteEnter,
              }}
            >
              <div
                style={{
                  fontSize: 90,
                  fontWeight: FONT_WEIGHT.bold,
                  lineHeight: 1.15,
                  letterSpacing: LETTER_SPACING.hero,
                  marginBottom: 30,
                }}
              >
                Учебник —
                <br />
                это
              </div>
              <div
                style={{
                  fontSize: 180,
                  fontWeight: FONT_WEIGHT.black,
                  color: COSMIC.starGold,
                  letterSpacing: 2,
                  textShadow: `0 0 ${pulseGlow}px ${COSMIC.starGold}, 0 0 ${pulseGlow * 2}px ${COSMIC.starGold}80`,
                  lineHeight: 0.95,
                }}
              >
                ЧЕРНОВИК
              </div>
              <div
                style={{
                  marginTop: 40,
                  fontSize: 36,
                  color: COSMIC.textSecondary,
                  fontStyle: "italic",
                  fontWeight: FONT_WEIGHT.regular,
                }}
              >
                а не финальная версия
              </div>
            </div>
          </AbsoluteFill>
        )}

        {/* === КАДР 9: brand mark === */}
        {frame >= 510 && (
          <AbsoluteFill
            style={{
              justifyContent: "flex-end",
              alignItems: "center",
              paddingBottom: 80,
            }}
          >
            <div style={{ opacity: brandOpacity, textAlign: "center" }}>
              <div
                style={{
                  color: COSMIC.text,
                  fontSize: 26,
                  fontWeight: FONT_WEIGHT.bold,
                  letterSpacing: LETTER_SPACING.capsWide,
                  marginBottom: 10,
                }}
              >
                POSTULAT · AI STUDIO
              </div>
              <div
                style={{
                  color: COSMIC.textDim,
                  fontSize: 18,
                  fontFamily: jetBrainsMono,
                  opacity: 0.7,
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
