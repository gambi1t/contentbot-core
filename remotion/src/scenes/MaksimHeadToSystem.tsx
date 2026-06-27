/**
 * MaksimHeadToSystem — сцена 4 полного B-roll ролика Максима.
 *
 * Под закадр: «Раньше операционка сидела в голове. Теперь — в системе.
 * Голова свободна для решений».
 *
 * 7 сек, 210 frames @ 30fps, 1080×1920.
 *   0.0–1.3с (0-40)    — верхний блок «РАНЬШЕ · В ГОЛОВЕ» — хаос карточек
 *   1.3–3.7с (40-110)  — хаос дрожит; снизу проступает «ТЕПЕРЬ · В СИСТЕМЕ»
 *   3.7–5.0с (110-150) — хаос гаснет, система остаётся чистой
 *   5.0–7.0с (150-210) — финальный акцент «Голова свободна для решений»
 *
 * Стиль: Постулат-dark (#0a0a0a + accent #ff5722 + Inter Tight).
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

export type MaksimHeadToSystemProps = {
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

// Хаотичные карточки «в голове» — детерминированные позиции.
const CHAOS = [
  { text: "смета", x: -210, y: -30 },
  { text: "звонок клиенту", x: 150, y: -90 },
  { text: "бронь домов", x: -120, y: 70 },
  { text: "зарплата", x: 200, y: 60 },
  { text: "ремонт карта", x: -230, y: 150 },
  { text: "поставщик", x: 120, y: 160 },
];

// Ровные строки «в системе».
const SYSTEM_ROWS = ["Смета на трассу", "Бронь на выходные", "Закупка экипировки", "Счёт поставщику"];

export const MaksimHeadToSystem: React.FC<MaksimHeadToSystemProps> = () => {
  const frame = useCurrentFrame();

  // Верхний блок «в голове» (0-40)
  const chaosIn = interpolate(frame, [0, 40], [0, 1], {
    extrapolateRight: "clamp",
  });
  // Хаос гаснет (110-145)
  const chaosOut = interpolate(frame, [110, 145], [1, 0.16], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Нижний блок «в системе» (80-150)
  const sysIn = interpolate(frame, [80, 150], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Стрелка (60-95)
  const arrowIn = interpolate(frame, [60, 95], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Финальный акцент (150-185)
  const finalIn = interpolate(frame, [150, 185], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bg,
        fontFamily: interTight,
        overflow: "hidden",
      }}
    >
      {/* === ВЕРХ: «В ГОЛОВЕ» — хаос === */}
      <div
        style={{
          position: "absolute",
          top: 250,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: chaosIn * chaosOut,
        }}
      >
        <div
          style={{
            color: colors.textDim,
            fontSize: 30,
            fontWeight: 700,
            letterSpacing: 4,
            marginBottom: 40,
          }}
        >
          РАНЬШЕ · В ГОЛОВЕ
        </div>
        <div style={{ position: "relative", height: 380 }}>
          {CHAOS.map((c, i) => {
            // лёгкое дрожание хаоса
            const jitterX = Math.sin(frame / 7 + i * 1.7) * 7;
            const jitterY = Math.cos(frame / 8 + i) * 6;
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: "50%",
                  top: "50%",
                  transform: `translate(${c.x + jitterX - 90}px, ${c.y + jitterY}px) rotate(${i % 2 ? 6 : -6}deg)`,
                  padding: "16px 24px",
                  backgroundColor: colors.card,
                  border: `2px solid ${colors.accent}`,
                  borderRadius: 14,
                  color: colors.text,
                  fontSize: 28,
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                {c.text}
              </div>
            );
          })}
        </div>
      </div>

      {/* === Стрелка-переход === */}
      <div
        style={{
          position: "absolute",
          top: 890,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: arrowIn,
        }}
      >
        <div style={{ color: colors.accent, fontSize: 64, fontWeight: 800 }}>↓</div>
      </div>

      {/* === НИЗ: «В СИСТЕМЕ» — порядок === */}
      <div
        style={{
          position: "absolute",
          top: 1010,
          left: 90,
          right: 90,
          opacity: sysIn,
          transform: `translateY(${(1 - ease(sysIn)) * 50}px)`,
        }}
      >
        <div
          style={{
            color: colors.textDim,
            fontSize: 30,
            fontWeight: 700,
            letterSpacing: 4,
            textAlign: "center",
            marginBottom: 28,
          }}
        >
          ТЕПЕРЬ · В СИСТЕМЕ
        </div>
        <div
          style={{
            backgroundColor: colors.card,
            border: `2px solid ${colors.border}`,
            borderRadius: 24,
            padding: "28px 32px",
            display: "flex",
            flexDirection: "column",
            gap: 16,
          }}
        >
          {SYSTEM_ROWS.map((row, i) => {
            const rowP = interpolate(
              frame,
              [95 + i * 12, 95 + i * 12 + 18],
              [0, 1],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
            );
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 18,
                  padding: "16px 20px",
                  backgroundColor: colors.bg,
                  borderRadius: 14,
                  opacity: rowP,
                }}
              >
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 18,
                    flexShrink: 0,
                    backgroundColor: colors.accent,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: colors.text,
                    fontSize: 22,
                    fontWeight: 800,
                  }}
                >
                  ✓
                </div>
                <span style={{ color: colors.text, fontSize: 30, fontWeight: 600 }}>
                  {row}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* === Финальный акцент === */}
      {finalIn > 0 && (
        <div
          style={{
            position: "absolute",
            bottom: 150,
            left: 0,
            right: 0,
            textAlign: "center",
            opacity: finalIn,
            transform: `translateY(${(1 - ease(finalIn)) * 30}px)`,
            padding: "0 70px",
          }}
        >
          <div style={{ color: colors.text, fontSize: 56, fontWeight: 800, lineHeight: 1.25 }}>
            Голова свободна
            <br />
            <span style={{ color: colors.accent }}>для решений</span>
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};
