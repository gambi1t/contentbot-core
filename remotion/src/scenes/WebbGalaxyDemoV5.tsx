/**
 * WebbGalaxyDemoV5 — финальная сцена.
 *
 * = V3 (красивая галактика + анимации работали) МИНУС дублирующий текст.
 *
 * Что выкинуто vs V3:
 *   - Hero headline 110-160px («Webb нашёл галактику которая НЕ ВРАЩАЕТСЯ»)
 *   - Glitch-overlay «НЕ ВРАЩАЕТСЯ» 180px
 *   - Quote «Учебник — это ЧЕРНОВИК» 180px с pulsing glow
 *   - Полноширинные fact-cards «ИМЯ ГАЛАКТИКИ / СКОРОСТЬ ВРАЩЕНИЯ» 56-80px
 *   - Subtitle «Так не должно быть. WEBB · 2026»
 *
 * Что осталось vs V3:
 *   - BREATH (одна звезда)
 *   - Zoom-out до starfield
 *   - Tag «🔭 JAMES WEBB · 2026» (маленький, в начале, для контекста)
 *   - Cartwheel real photo + Ken Burns slow zoom
 *   - Cartwheel slow rotation 0→14° → vinyl-stop с camera shake + flash
 *   - Pulsing glow на Cartwheel (момент откровения)
 *   - Tech-chip XMM-VID1-2075 в углу (single text element)
 *   - Финальное затемнение → brand mark
 *
 * Концепция: voice ведёт нарратив, B-roll — визуальная сцена под ним.
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

// ---------- Starfield ----------
const StarField: React.FC<{ density?: number; opacity?: number }> = ({
  density = 250,
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
              opacity: twinkle * 0.85 * opacity,
              boxShadow: isGold ? `0 0 ${size * 4}px ${COSMIC.starGold}` : "none",
            }}
          />
        );
      })}
    </>
  );
};

export type WebbGalaxyDemoV5Props = {
  [key: string]: unknown;
};

export const WebbGalaxyDemoV5: React.FC<WebbGalaxyDemoV5Props> = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // КАДР 1 (0-30): BREATH — одна звезда
  // ============================================================================
  const pointOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });

  // ============================================================================
  // КАДР 2 (30-90): zoom-out → starfield + tag
  // ============================================================================
  const zoomOut = interpolate(frame, [30, 90], [12, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });
  const starfieldOpacity = interpolate(frame, [30, 80], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Маленький tag — «контекст» для зрителя кто это снял
  const tagSpring = spring({
    frame: frame - 50,
    fps,
    config: SPRING.snappy,
  });
  const tagExit = interpolate(frame, [120, 140], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // CARTWHEEL: enter (90-150), Ken Burns на всё видео
  // ============================================================================
  const cartwheelEnter = interpolate(frame, [90, 150], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });
  const cartwheelEnterScale = interpolate(frame, [90, 150], [0.3, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });

  // Ken Burns: 1.0 → 1.2 за 150-450 (10 секунд медленный zoom)
  const kenBurnsScale =
    frame < 150
      ? 1.0
      : interpolate(frame, [150, 450], [1.0, 1.2], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.outCubic,
        });

  // ============================================================================
  // ROTATION: 0→14° за 180-300, FREEZE на 300 (vinyl stop)
  // ============================================================================
  let cartwheelRotation = 0;
  if (frame >= 180 && frame < 300) {
    cartwheelRotation = interpolate(frame, [180, 300], [0, 14], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASING.outCubic,
    });
  } else if (frame >= 300) {
    cartwheelRotation = 14;
  }

  // Camera shake on stop: 30px decay 24 frames
  const stopShake =
    frame >= 300 && frame <= 324
      ? Math.sin((frame - 300) * 1.5) * 30 * (1 - (frame - 300) / 24)
      : 0;

  // White flash: 8 frames peak 0.85
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

  // Pulsing glow на Cartwheel — после freeze, медленно пульсирует золотом
  const cartwheelGlow =
    frame >= 360
      ? 30 + 70 * (0.5 + 0.5 * Math.sin((frame - 360) * 0.2))
      : 0;

  // ============================================================================
  // TECH CHIP — единственный текст в кадре (frames 360-450)
  // ============================================================================
  const chipSpring = spring({ frame: frame - 360, fps, config: SPRING.snappy });
  const chipExit = interpolate(frame, [440, 460], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // FINAL: Cartwheel zooms out → точка → fade
  // ============================================================================
  const finalZoom =
    frame >= 460
      ? interpolate(frame, [460, 510], [1, 0.05], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.cinematic,
        })
      : 1;
  const finalCartwheelOpacity =
    frame >= 500
      ? interpolate(frame, [500, 515], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;
  const brandOpacity = interpolate(frame, [515, 535], [0, 1], {
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
      {/* Background gradient */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at 50% 50%, ${COSMIC.bgLight}80 0%, ${COSMIC.bgMid} 35%, ${COSMIC.bgDeep} 100%)`,
        }}
      />

      {/* Camera shake wrapper */}
      <AbsoluteFill
        style={{
          transform: `translate(${stopShake}px, ${-stopShake * 0.5}px)`,
        }}
      >
        {/* === КАДР 1: BREATH point === */}
        {frame < 90 && (
          <AbsoluteFill
            style={{
              alignItems: "center",
              justifyContent: "center",
              transform: `scale(${frame < 30 ? 1 : zoomOut})`,
            }}
          >
            <div
              style={{
                width: 14,
                height: 14,
                borderRadius: "50%",
                backgroundColor: COSMIC.starGold,
                boxShadow: `0 0 50px ${COSMIC.starGold}, 0 0 120px ${COSMIC.starWarm}`,
                opacity: pointOpacity,
              }}
            />
          </AbsoluteFill>
        )}

        {/* === STARFIELD === */}
        {frame >= 30 && (
          <AbsoluteFill
            style={{
              opacity:
                starfieldOpacity *
                (frame >= 500
                  ? interpolate(frame, [500, 515], [1, 0.4], {
                      extrapolateRight: "clamp",
                    })
                  : 1),
            }}
          >
            <StarField density={250} />
          </AbsoluteFill>
        )}

        {/* === TAG «🔭 JAMES WEBB · 2026» (маленький, для контекста) === */}
        {frame >= 50 && frame < 145 && (
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
                transform: `translateY(${(1 - tagSpring) * -25}px)`,
                marginTop: 60,
                padding: "12px 24px",
                border: `1.5px solid ${COSMIC.galaxyPurple}`,
                borderRadius: 100,
                backgroundColor: "rgba(157,78,221,0.12)",
                color: COSMIC.text,
                fontSize: 24,
                fontWeight: FONT_WEIGHT.semibold,
                letterSpacing: LETTER_SPACING.capsWide,
                fontFamily: jetBrainsMono,
              }}
            >
              🔭 JAMES WEBB · 2026
            </div>
          </AbsoluteFill>
        )}

        {/* === CARTWHEEL — главный визуал === */}
        {frame >= 90 && (
          <AbsoluteFill
            style={{
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <div
              style={{
                width: 1080,
                height: 1080,
                opacity: cartwheelEnter * finalCartwheelOpacity,
                transform: `scale(${cartwheelEnterScale * kenBurnsScale * finalZoom}) rotate(${cartwheelRotation}deg)`,
                position: "relative",
                overflow: "hidden",
                borderRadius: 12,
                filter:
                  cartwheelGlow > 0
                    ? `drop-shadow(0 0 ${cartwheelGlow}px ${COSMIC.starGold})`
                    : "none",
              }}
            >
              <Img
                src={staticFile("images/nasa-cartwheel.jpg")}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  filter: "saturate(1.4) contrast(1.15) brightness(1.0)",
                }}
              />
              {/* Vignette overlay */}
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  background: `radial-gradient(circle, transparent 45%, ${COSMIC.bgDeep}E0 95%)`,
                  pointerEvents: "none",
                }}
              />
            </div>
          </AbsoluteFill>
        )}

        {/* === MEGA WHITE FLASH on stop === */}
        {flashOpacity > 0 && (
          <AbsoluteFill
            style={{
              backgroundColor: "#ffffff",
              opacity: flashOpacity,
            }}
          />
        )}

        {/* === TECH CHIP — единственный текст ===
            XMM-VID1-2075 · V_ROT ≈ 0
            Появляется внизу когда galaxy уже зафризена и пульсирует */}
        {frame >= 360 && frame < 460 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "flex-end",
              alignItems: "center",
              paddingBottom: 220,
            }}
          >
            <div
              style={{
                opacity: chipSpring * chipExit,
                transform: `translateY(${(1 - chipSpring) * 20}px)`,
                padding: "18px 28px",
                backgroundColor: "rgba(13,18,64,0.9)",
                border: `2px solid ${COSMIC.galaxyBlue}`,
                borderRadius: 14,
                backdropFilter: "blur(10px)",
                boxShadow: `0 12px 40px rgba(0,0,0,0.6), 0 0 30px ${COSMIC.galaxyBlue}40`,
                display: "flex",
                gap: 24,
                alignItems: "center",
                fontFamily: jetBrainsMono,
              }}
            >
              <div>
                <div
                  style={{
                    color: COSMIC.galaxyBlue,
                    fontSize: 14,
                    fontWeight: FONT_WEIGHT.bold,
                    letterSpacing: LETTER_SPACING.capsWide,
                    marginBottom: 2,
                  }}
                >
                  GALAXY ID
                </div>
                <div
                  style={{
                    color: COSMIC.text,
                    fontSize: 26,
                    fontWeight: FONT_WEIGHT.bold,
                  }}
                >
                  XMM-VID1-2075
                </div>
              </div>
              <div
                style={{
                  width: 1,
                  height: 40,
                  backgroundColor: COSMIC.galaxyBlue,
                  opacity: 0.5,
                }}
              />
              <div>
                <div
                  style={{
                    color: COSMIC.starGold,
                    fontSize: 14,
                    fontWeight: FONT_WEIGHT.bold,
                    letterSpacing: LETTER_SPACING.capsWide,
                    marginBottom: 2,
                  }}
                >
                  V_ROT
                </div>
                <div
                  style={{
                    color: COSMIC.starGold,
                    fontSize: 26,
                    fontWeight: FONT_WEIGHT.bold,
                  }}
                >
                  ≈ 0
                </div>
              </div>
            </div>
          </AbsoluteFill>
        )}

        {/* === FINAL BRAND MARK === */}
        {frame >= 515 && (
          <AbsoluteFill
            style={{
              justifyContent: "center",
              alignItems: "center",
            }}
          >
            <div style={{ opacity: brandOpacity, textAlign: "center" }}>
              <div
                style={{
                  color: COSMIC.text,
                  fontSize: 24,
                  fontWeight: FONT_WEIGHT.semibold,
                  letterSpacing: LETTER_SPACING.capsWide,
                  marginBottom: 8,
                  fontFamily: interTight,
                }}
              >
                POSTULAT · AI STUDIO
              </div>
              <div
                style={{
                  color: COSMIC.textDim,
                  fontSize: 16,
                  fontFamily: jetBrainsMono,
                  opacity: 0.7,
                }}
              >
                data: NASA / JWST · 2026
              </div>
            </div>
          </AbsoluteFill>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
