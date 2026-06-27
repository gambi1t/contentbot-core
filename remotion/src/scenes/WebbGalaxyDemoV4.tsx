/**
 * WebbGalaxyDemoV4 — переделка после фидбэка Артёма "не дублируй текст".
 *
 * Принципы:
 *   - B-roll ПОКАЗЫВАЕТ, не повторяет голос аватара
 *   - Текст в кадре ТОЛЬКО для того что устно сложно (ID, числа, source)
 *   - Драма через визуальную анимацию, не через большие буквы
 *   - Главный приём: 5-6 силуэт-галактик вращаются вокруг,
 *     наша Cartwheel резко стопается → визуальный контраст без слов
 *
 * 18 сек / 540 frames @ 30fps / 1080×1920
 *
 * Storyboard:
 *   0.0–2.0с (frames   0–60)  Точка → starfield zoom-out
 *   2.0–4.0с (frames  60–120) Камера летит сквозь звёзды → Cartwheel растёт
 *   4.0–7.0с (frames 120–210) Cartwheel + 5 silhouette-галактик вокруг, ВСЕ вращаются
 *   7.0–8.0с (frames 210–240) Cartwheel резко стопается + camera shake + flash
 *   8.0–12.0с (frames 240–360) Cartwheel стоит, силуэты КРУТЯТСЯ — контраст
 *   12.0–14.0с (frames 360–420) Тонкий chip с techn-данными в углу
 *   14.0–16.0с (frames 420–480) Cartwheel ПУЛЬСИРУЕТ золотом
 *   16.0–17.5с (frames 480–525) Камера отъезжает, Cartwheel становится точкой
 *   17.5–18.0с (frames 525–540) Brand mark
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
const StarField: React.FC<{ density?: number; opacity?: number; seed?: string }> = ({
  density = 200,
  opacity = 1,
  seed = "main",
}) => {
  const frame = useCurrentFrame();
  return (
    <>
      {Array.from({ length: density }).map((_, i) => {
        const x = random(`${seed}-x-${i}`) * 1080;
        const y = random(`${seed}-y-${i}`) * 1920;
        const size = 1.2 + random(`${seed}-s-${i}`) * 2.5;
        const isGold = random(`${seed}-c-${i}`) > 0.88;
        const phase = random(`${seed}-p-${i}`) * Math.PI * 2;
        const twinkle = 0.55 + 0.45 * Math.sin(frame * 0.07 + phase);
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

// ---------- Silhouette galaxies (вокруг Cartwheel, вращаются) ----------
type SilhouetteSpec = {
  cx: number; // center x in 1080
  cy: number; // center y in 1920
  size: number;
  color: string;
  rotationSpeed: number; // deg per frame
  ellipseRatio: number; // 0.4-0.7 для перспективы
  delay: number; // frames до появления
  freezeAt?: number; // если задано, эта силуэтка тоже стопается
};

const SILHOUETTES: SilhouetteSpec[] = [
  { cx: 240, cy: 480, size: 220, color: COSMIC.galaxyPurple, rotationSpeed: 0.4, ellipseRatio: 0.55, delay: 130 },
  { cx: 880, cy: 540, size: 180, color: COSMIC.galaxyBlue, rotationSpeed: -0.55, ellipseRatio: 0.62, delay: 145 },
  { cx: 200, cy: 1320, size: 240, color: COSMIC.galaxyPurple, rotationSpeed: -0.35, ellipseRatio: 0.5, delay: 160 },
  { cx: 900, cy: 1380, size: 200, color: COSMIC.galaxyBlue, rotationSpeed: 0.45, ellipseRatio: 0.58, delay: 175 },
  { cx: 540, cy: 240, size: 160, color: COSMIC.galaxyPurple, rotationSpeed: 0.3, ellipseRatio: 0.65, delay: 190 },
  { cx: 540, cy: 1640, size: 170, color: COSMIC.galaxyBlue, rotationSpeed: -0.5, ellipseRatio: 0.6, delay: 200 },
];

const SilhouetteGalaxy: React.FC<{
  spec: SilhouetteSpec;
  frame: number;
  cartwheelExitOpacity: number;
}> = ({ spec, frame, cartwheelExitOpacity }) => {
  if (frame < spec.delay) return null;
  const enterOpacity = interpolate(frame, [spec.delay, spec.delay + 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rotation = (frame - spec.delay) * spec.rotationSpeed;
  return (
    <div
      style={{
        position: "absolute",
        left: spec.cx - spec.size / 2,
        top: spec.cy - spec.size / 2,
        width: spec.size,
        height: spec.size,
        opacity: enterOpacity * 0.55 * cartwheelExitOpacity,
        transform: `rotate(${rotation}deg)`,
      }}
    >
      {/* Outer glow */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: "50%",
          background: `radial-gradient(circle, ${spec.color}50 0%, transparent 65%)`,
          filter: "blur(20px)",
        }}
      />
      {/* Spiral arm — соответствует rotation */}
      <div
        style={{
          position: "absolute",
          inset: "20% 5%",
          borderRadius: "50%",
          background: `radial-gradient(ellipse, ${spec.color}90 0%, transparent 70%)`,
          transform: `scaleY(${spec.ellipseRatio})`,
          filter: "blur(4px)",
        }}
      />
      {/* Small core */}
      <div
        style={{
          position: "absolute",
          inset: "42%",
          borderRadius: "50%",
          backgroundColor: COSMIC.starWarm,
          boxShadow: `0 0 16px ${COSMIC.starWarm}`,
        }}
      />
    </div>
  );
};

export type WebbGalaxyDemoV4Props = {
  [key: string]: unknown;
};

export const WebbGalaxyDemoV4: React.FC<WebbGalaxyDemoV4Props> = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // КАДР 1 (0-30): BREATH point → expand
  // ============================================================================
  const pointOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });

  // ============================================================================
  // КАДР 2 (30-120): zoom-out + flight through stars + Cartwheel grows
  // ============================================================================
  const flightZoom = interpolate(frame, [30, 90], [10, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });
  const starfieldOpacity = interpolate(frame, [30, 80], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Cartwheel growth: появляется как точка на frame 70, растёт до полного размера к frame 130
  const cartwheelEnter = interpolate(frame, [70, 130], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });

  // ============================================================================
  // Cartwheel rotation: 0→14° за frames 130-210, FREEZE на 210
  // ============================================================================
  let cartwheelRotation = 0;
  if (frame >= 130 && frame < 210) {
    cartwheelRotation = interpolate(frame, [130, 210], [0, 14], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASING.outCubic,
    });
  } else if (frame >= 210) {
    cartwheelRotation = 14;
  }

  // Camera shake УСИЛЕН: 30px on stop, decay 24 frames
  const stopShake =
    frame >= 210 && frame <= 234
      ? Math.sin((frame - 210) * 1.4) * 30 * (1 - (frame - 210) / 24)
      : 0;

  // Mega white flash: 8 frames peak 0.85
  const flashOpacity = interpolate(
    frame,
    [208, 212, 220],
    [0, 0.85, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASING.fastOut,
    },
  );

  // Ken Burns slow zoom на Cartwheel — постоянный, всё время до finale
  const kenBurnsScale =
    frame < 70
      ? 1.0
      : interpolate(frame, [70, 480], [1.0, 1.25], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.outCubic,
        });

  // Pulse ramp на Cartwheel начиная с frame 420
  const cartwheelPulse =
    frame >= 420 && frame < 480
      ? 0.6 + 0.4 * Math.sin((frame - 420) * 0.3)
      : 1;

  // Final zoom-out: Cartwheel становится точкой
  const finalZoomScale =
    frame >= 480
      ? interpolate(frame, [480, 525], [1, 0.04], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.cinematic,
        })
      : 1;
  const finalCartwheelOpacity =
    frame >= 510
      ? interpolate(frame, [510, 525], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;

  // ============================================================================
  // Tech-chip (frames 360-420 only, в углу, минимальный)
  // ============================================================================
  const chipSpring = spring({ frame: frame - 360, fps, config: SPRING.snappy });
  const chipExit = interpolate(frame, [410, 425], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // Final brand-mark (frames 525-540)
  // ============================================================================
  const brandOpacity = interpolate(frame, [525, 540], [0, 1], {
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
        {/* === КАДР 1: BREATH point in center === */}
        {frame < 90 && (
          <AbsoluteFill
            style={{
              alignItems: "center",
              justifyContent: "center",
              transform: `scale(${frame < 30 ? 1 : flightZoom})`,
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

        {/* === STARFIELD (после zoom-out) === */}
        {frame >= 30 && (
          <AbsoluteFill style={{ opacity: starfieldOpacity * (frame >= 510 ? interpolate(frame, [510, 525], [1, 0.4], { extrapolateRight: "clamp" }) : 1) }}>
            <StarField density={250} seed="main" />
          </AbsoluteFill>
        )}

        {/* === SILHOUETTE GALAXIES (вокруг, вращаются) === */}
        {frame >= 130 && frame < 480 &&
          SILHOUETTES.map((spec, i) => (
            <SilhouetteGalaxy
              key={i}
              spec={spec}
              frame={frame}
              cartwheelExitOpacity={interpolate(
                frame,
                [460, 480],
                [1, 0],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
              )}
            />
          ))}

        {/* === CARTWHEEL (центральный hero) === */}
        {frame >= 70 && (
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
                transform: `scale(${kenBurnsScale * finalZoomScale * cartwheelPulse}) rotate(${cartwheelRotation}deg)`,
                position: "relative",
                overflow: "hidden",
                borderRadius: 12,
                filter: cartwheelPulse > 1
                  ? `drop-shadow(0 0 ${(cartwheelPulse - 1) * 100}px ${COSMIC.starGold})`
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

        {/* === TECH CHIP (минимальный, 12-14 сек) === */}
        {frame >= 360 && frame < 425 && (
          <AbsoluteFill
            style={{
              padding: SAFE_AREA,
              justifyContent: "flex-end",
              alignItems: "center",
              paddingBottom: 240,
            }}
          >
            <div
              style={{
                opacity: chipSpring * chipExit,
                transform: `translateY(${(1 - chipSpring) * 20}px)`,
                padding: "18px 28px",
                backgroundColor: "rgba(13,18,64,0.85)",
                border: `2px solid ${COSMIC.galaxyBlue}`,
                borderRadius: 14,
                backdropFilter: "blur(10px)",
                boxShadow: `0 12px 40px rgba(0,0,0,0.6)`,
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
                    fontSize: 24,
                    fontWeight: FONT_WEIGHT.bold,
                  }}
                >
                  XMM-VID1-2075
                </div>
              </div>
              <div
                style={{
                  width: 1,
                  height: 36,
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
                    fontSize: 24,
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
        {frame >= 525 && (
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
                  fontSize: 22,
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
