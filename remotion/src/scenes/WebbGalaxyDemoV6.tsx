/**
 * WebbGalaxyDemoV6 — путешествие через вселенную.
 *
 * Фидбэк Артёма: «каждые 2-3 секунды должно что-то меняться, картинка развиваться».
 *
 * Storyboard 9 объектов за 18 сек (новый visual каждые ~2 сек):
 *   0.0–2.0   Точка → starfield (zoom-out 12x)
 *   2.0–4.0   Полёт сквозь звёзды → Pillars of Creation появляется
 *   4.0–6.0   Ныряем глубже → Carina Nebula (другая туманность)
 *   6.0–8.0   Выныриваем → впереди Cartwheel-галактика растёт
 *   8.0–10.0  Cartwheel вращается + tag JAMES WEBB
 *   10.0–11.0 Vinyl-stop + flash + camera shake (УДАР)
 *   11.0–13.0 Cartwheel пульсирует золотом + tech-chip XMM-VID1-2075
 *   13.0–15.0 Зум в центр галактики (детали)
 *   15.0–17.0 Резкий zoom-out → Cartwheel становится точкой
 *   17.0–18.0 Brand mark на звёздном фоне
 *
 * Использует все 3 NASA-фото: Pillars + Carina + Cartwheel.
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

// Starfield reusable
const StarField: React.FC<{ density?: number; opacity?: number; seed?: string }> = ({
  density = 250,
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
        const isGold = random(`${seed}-c-${i}`) > 0.85;
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

// Космический объект (туманность/галактика) — fly-by приём
const CosmicObject: React.FC<{
  src: string;
  startFrame: number;
  endFrame: number;
  startScale: number;
  endScale: number;
  startOpacity: number;
  endOpacity: number;
  saturate?: number;
  contrast?: number;
}> = ({
  src,
  startFrame,
  endFrame,
  startScale,
  endScale,
  startOpacity,
  endOpacity,
  saturate = 1.4,
  contrast = 1.15,
}) => {
  const frame = useCurrentFrame();
  if (frame < startFrame || frame > endFrame) return null;

  const scale = interpolate(frame, [startFrame, endFrame], [startScale, endScale], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });
  const opacity = interpolate(frame, [startFrame, endFrame], [startOpacity, endOpacity], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          width: 1080,
          height: 1080,
          opacity,
          transform: `scale(${scale})`,
          position: "relative",
          overflow: "hidden",
          borderRadius: 12,
        }}
      >
        <Img
          src={staticFile(src)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            filter: `saturate(${saturate}) contrast(${contrast})`,
          }}
        />
        {/* Vignette */}
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
  );
};

export type WebbGalaxyDemoV6Props = {
  [key: string]: unknown;
};

export const WebbGalaxyDemoV6: React.FC<WebbGalaxyDemoV6Props> = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ============================================================================
  // КАДР 1 (0-30, 0-1с): BREATH — одна звезда
  // ============================================================================
  const pointOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp",
    easing: EASING.outCubic,
  });
  const breathZoom = interpolate(frame, [30, 60], [12, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
  });

  // ============================================================================
  // КАДР 2 (60-120, 2-4с): Pillars of Creation проносится
  // (Pillars-сцена в CosmicObject ниже)
  // ============================================================================

  // ============================================================================
  // КАДР 3 (120-180, 4-6с): Carina Nebula дальше
  // ============================================================================

  // ============================================================================
  // КАДР 4-5 (180-300, 6-10с): Cartwheel растёт + slow rotation
  // ============================================================================
  // Cartwheel rotation 0→14° за 240-300, freeze на 300
  let cartwheelRotation = 0;
  if (frame >= 240 && frame < 300) {
    cartwheelRotation = interpolate(frame, [240, 300], [0, 14], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASING.outCubic,
    });
  } else if (frame >= 300) {
    cartwheelRotation = 14;
  }

  // Tag JAMES WEBB (frames 200-280)
  const tagSpring = spring({ frame: frame - 200, fps, config: SPRING.snappy });
  const tagExit = interpolate(frame, [275, 290], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 6 (300-330, 10-11с): vinyl-stop + flash + shake
  // ============================================================================
  const stopShake =
    frame >= 300 && frame <= 324
      ? Math.sin((frame - 300) * 1.5) * 30 * (1 - (frame - 300) / 24)
      : 0;
  const flashOpacity = interpolate(
    frame,
    [298, 302, 308],
    [0, 0.85, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASING.fastOut },
  );

  // ============================================================================
  // КАДР 7 (330-390, 11-13с): pulse + tech-chip
  // ============================================================================
  const cartwheelGlow =
    frame >= 330 && frame < 450
      ? 30 + 70 * (0.5 + 0.5 * Math.sin((frame - 330) * 0.2))
      : 0;
  const chipSpring = spring({ frame: frame - 330, fps, config: SPRING.snappy });
  const chipExit = interpolate(frame, [380, 400], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // ============================================================================
  // КАДР 8 (390-450, 13-15с): zoom-in в центр Cartwheel
  // ============================================================================
  // На frame 390 начинаем zoom-in: 1.0 → 2.0
  const cartwheelZoomIn =
    frame >= 390 && frame < 450
      ? interpolate(frame, [390, 450], [1.0, 2.0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.cinematic,
        })
      : frame >= 240 && frame < 390
      ? interpolate(frame, [240, 390], [1.0, 1.15], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: EASING.outCubic,
        })
      : 1.0;

  // ============================================================================
  // КАДР 9 (450-510, 15-17с): резкий zoom-out → Cartwheel становится точкой
  // ============================================================================
  const finalZoom =
    frame >= 450
      ? interpolate(frame, [450, 510], [2.0, 0.04], {
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

  // ============================================================================
  // КАДР 10 (510-540, 17-18с): Brand mark
  // ============================================================================
  const brandOpacity = interpolate(frame, [515, 535], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Cartwheel intro fade (frames 180-240): растёт из глубины
  const cartwheelEnter = interpolate(frame, [180, 240], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASING.cinematic,
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
        {frame < 60 && (
          <AbsoluteFill
            style={{
              alignItems: "center",
              justifyContent: "center",
              transform: `scale(${frame < 30 ? 1 : breathZoom})`,
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

        {/* === STARFIELD (под всеми объектами) === */}
        {frame >= 30 && (
          <AbsoluteFill
            style={{
              opacity:
                frame >= 60 && frame < 180
                  ? interpolate(frame, [60, 180], [1, 0.3], {
                      extrapolateRight: "clamp",
                    })
                  : frame >= 500
                  ? interpolate(frame, [500, 540], [0.3, 1], {
                      extrapolateRight: "clamp",
                    })
                  : 1,
            }}
          >
            <StarField density={250} seed="main" />
          </AbsoluteFill>
        )}

        {/* === КАДР 2 (60-130): Pillars of Creation fly-by ===
             Появляется маленьким с глубины, растёт, проносится мимо */}
        <CosmicObject
          src="images/nasa-pillars.jpg"
          startFrame={60}
          endFrame={130}
          startScale={0.2}
          endScale={2.0}
          startOpacity={0}
          endOpacity={0}
        />

        {/* === Pillars в полной видимости (75-115) === */}
        <CosmicObject
          src="images/nasa-pillars.jpg"
          startFrame={75}
          endFrame={115}
          startScale={0.4}
          endScale={1.5}
          startOpacity={0.95}
          endOpacity={0.95}
        />

        {/* === КАДР 3 (120-180): Carina Nebula fly-by === */}
        <CosmicObject
          src="images/nasa-carina.jpg"
          startFrame={120}
          endFrame={190}
          startScale={0.3}
          endScale={2.2}
          startOpacity={0}
          endOpacity={0}
          saturate={1.3}
        />

        <CosmicObject
          src="images/nasa-carina.jpg"
          startFrame={135}
          endFrame={175}
          startScale={0.5}
          endScale={1.6}
          startOpacity={0.95}
          endOpacity={0.95}
          saturate={1.3}
        />

        {/* === CARTWHEEL (180-510): главный hero === */}
        {frame >= 180 && (
          <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
            <div
              style={{
                width: 1080,
                height: 1080,
                opacity: cartwheelEnter * finalCartwheelOpacity,
                transform: `scale(${cartwheelZoomIn * finalZoom}) rotate(${cartwheelRotation}deg)`,
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

        {/* === TAG «🔭 JAMES WEBB · 2026» (200-290, 6.5-9.5с) === */}
        {frame >= 200 && frame < 295 && (
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

        {/* === MEGA WHITE FLASH on stop === */}
        {flashOpacity > 0 && (
          <AbsoluteFill
            style={{
              backgroundColor: "#ffffff",
              opacity: flashOpacity,
            }}
          />
        )}

        {/* === TECH CHIP (330-400, 11-13с) === */}
        {frame >= 330 && frame < 400 && (
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
