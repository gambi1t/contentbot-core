/**
 * MaksimHookChaos — сцена 1 (хук) полного B-roll ролика Максима.
 *
 * Под закадр: «Я уволил себя из роли надзирателя. Контроль стал качественнее,
 * появилось время на то, чем должен заниматься собственник».
 *
 * 9 сек, 270 frames @ 30fps, 1080×1920.
 *   0.0–1.0с (0-30)     — заголовок «РУКОВОДИТЕЛЬ» + счётчик
 *   1.0–3.7с (30-110)   — карточки-уведомления налетают, счётчик растёт (хаос)
 *   3.7–5.0с (110-150)  — всё замирает, проступает слово НАДЗИРАТЕЛЬ
 *   5.0–5.8с (150-175)  — НАДЗИРАТЕЛЬ перечёркнут, карточки осыпаются
 *   5.8–9.0с (175-270)  — чистый кадр, спокойный вывод
 *
 * Стиль: Постулат-dark (#0a0a0a + accent #ff5722 + Inter Tight).
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

export type MaksimHookChaosProps = {
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

type Notif = {
  text: string;
  x: number; // px от центра по X
  y: number; // px от центра по Y
  rot: number; // deg
  appear: number; // кадр появления
};

// Детерминированные позиции — никакого Math.random в рендере.
const NOTIFS: Notif[] = [
  { text: "Где отчёт?", x: -250, y: -420, rot: -8, appear: 32 },
  { text: "Срочно перезвони", x: 230, y: -340, rot: 7, appear: 42 },
  { text: "Задача висит 3 дня", x: -290, y: -180, rot: -5, appear: 52 },
  { text: "Согласуй смету", x: 250, y: -120, rot: 9, appear: 62 },
  { text: "Клиент ждёт ответ", x: -210, y: 60, rot: 6, appear: 72 },
  { text: "Почему не сделано?", x: 270, y: 130, rot: -7, appear: 82 },
  { text: "Подтверди заявку", x: -270, y: 300, rot: 8, appear: 92 },
  { text: "Перенесли планёрку", x: 220, y: 360, rot: -6, appear: 100 },
];

export const MaksimHookChaos: React.FC<MaksimHookChaosProps> = () => {
  const frame = useCurrentFrame();

  // Phase 1: заголовок fade-in (0-30)
  const headOpacity = interpolate(frame, [0, 30], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Счётчик уведомлений растёт по мере появления карточек (30-110)
  const counter = Math.round(
    interpolate(frame, [30, 110], [0, 47], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  // Phase 3: слово НАДЗИРАТЕЛЬ проступает (110-150)
  const wordOpacity = interpolate(frame, [110, 145], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Phase 4: перечёркивание (150-172)
  const strike = interpolate(frame, [150, 172], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Карточки осыпаются (152-180)
  const fall = interpolate(frame, [152, 182], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Phase 5: чистый вывод (175-270)
  const outroOpacity = interpolate(frame, [185, 215], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Слово НАДЗИРАТЕЛЬ уходит вместе с карточками
  const wordGone = interpolate(frame, [175, 195], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Заголовок плавно гаснет вместе с уходом хаоса (175-195) — без скачка
  const headerFade = interpolate(frame, [175, 195], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bg,
        fontFamily: interTight,
        justifyContent: "center",
        alignItems: "center",
        overflow: "hidden",
      }}
    >
      {/* Заголовок-бейдж + счётчик */}
      <div
        style={{
          position: "absolute",
          top: 150,
          display: "flex",
          alignItems: "center",
          gap: 20,
          opacity: headOpacity * headerFade,
        }}
      >
        <span
          style={{
            color: colors.textDim,
            fontSize: 30,
            fontWeight: 700,
            letterSpacing: 4,
          }}
        >
          РУКОВОДИТЕЛЬ
        </span>
        <div
          style={{
            padding: "8px 18px",
            borderRadius: 20,
            backgroundColor: colors.accent,
            color: colors.text,
            fontFamily: jetBrainsMono,
            fontSize: 28,
            fontWeight: 700,
          }}
        >
          {counter}
        </div>
      </div>

      {/* Карточки-уведомления */}
      {NOTIFS.map((n, i) => {
        const p = interpolate(frame, [n.appear, n.appear + 12], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        if (p <= 0) return null;
        const fallY = ease(fall) * (900 + i * 40);
        const op = p * (1 - fall);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              transform: `translate(${n.x}px, ${n.y + fallY}px) rotate(${n.rot}deg) scale(${0.7 + 0.3 * ease(p)})`,
              opacity: op,
              padding: "22px 30px",
              backgroundColor: colors.card,
              border: `2px solid ${colors.accent}`,
              borderRadius: 16,
              color: colors.text,
              fontSize: 32,
              fontWeight: 600,
              whiteSpace: "nowrap",
              boxShadow: "0 12px 30px rgba(0,0,0,0.5)",
            }}
          >
            {n.text}
          </div>
        );
      })}

      {/* Слово НАДЗИРАТЕЛЬ + перечёркивание */}
      {wordOpacity > 0 && (
        <div
          style={{
            position: "absolute",
            opacity: wordOpacity * wordGone,
            whiteSpace: "nowrap",
          }}
        >
          <span
            style={{
              color: colors.text,
              fontSize: 100,
              fontWeight: 800,
              fontStyle: "italic",
              letterSpacing: -2,
            }}
          >
            НАДЗИРАТЕЛЬ
          </span>
          {/* Линия перечёркивания */}
          <div
            style={{
              position: "absolute",
              top: "52%",
              left: 0,
              height: 12,
              width: `${strike * 100}%`,
              backgroundColor: colors.accent,
              borderRadius: 6,
            }}
          />
        </div>
      )}

      {/* Чистый вывод */}
      {outroOpacity > 0 && (
        <div
          style={{
            position: "absolute",
            textAlign: "center",
            opacity: outroOpacity,
            padding: "0 80px",
          }}
        >
          <div
            style={{
              color: colors.accent,
              fontSize: 34,
              fontWeight: 700,
              letterSpacing: 3,
              marginBottom: 24,
            }}
          >
            Я УВОЛИЛ СЕБЯ
          </div>
          <div style={{ color: colors.text, fontSize: 58, fontWeight: 800, lineHeight: 1.25 }}>
            Контроль — качественнее.
            <br />
            Время — на то, что важно.
          </div>
        </div>
      )}

      {/* Бренд-марка */}
      <div
        style={{
          position: "absolute",
          bottom: 60,
          left: 0,
          right: 0,
          textAlign: "center",
          color: colors.textDim,
          fontSize: 24,
          fontWeight: 600,
          letterSpacing: 4,
          opacity: headOpacity,
        }}
      >
        LIFE DRIVE
      </div>
    </AbsoluteFill>
  );
};
