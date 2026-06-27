/**
 * MaksimChannelCta — сцена 5 (CTA) полного B-roll ролика Максима.
 *
 * Под закадр: «Как собрал — в Telegram-канале "Юмсунов про реальный бизнес"».
 *
 * 5 сек, 150 frames @ 30fps, 1080×1920.
 *   0.0–1.0с (0-30)    — карточка канала fade-in
 *   1.0–2.7с (30-80)   — аватар, название, описание проступают
 *   2.3–3.7с (70-110)  — кнопка «Подписаться» появляется и пульсирует
 *   3.7–4.3с (110-130) — «тап» по кнопке → «✓ Вы подписаны»
 *   4.3–5.0с (130-150) — hold
 *
 * Стиль: Постулат-dark (#0a0a0a + accent #ff5722 + Inter Tight).
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

export type MaksimChannelCtaProps = {
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

export const MaksimChannelCta: React.FC<MaksimChannelCtaProps> = () => {
  const frame = useCurrentFrame();

  // Карточка канала (0-30)
  const cardIn = interpolate(frame, [0, 30], [0, 1], {
    extrapolateRight: "clamp",
  });
  const cardY = interpolate(frame, [0, 30], [50, 0], {
    extrapolateRight: "clamp",
  });

  // Кнопка «Подписаться» (70-100)
  const btnIn = interpolate(frame, [70, 100], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Пульс кнопки до нажатия (100-112)
  const subscribed = frame >= 116;
  const btnPulse =
    frame >= 100 && frame < 116 ? 1 + 0.04 * Math.sin((frame - 100) / 2) : 1;
  // «Тап» — расходящийся круг (112-132)
  const tap = interpolate(frame, [112, 132], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Нажатие — кнопка слегка вдавливается на кадрах 112-118
  const press =
    frame >= 112 && frame < 118 ? 0.95 : 1;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bg,
        fontFamily: interTight,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {/* Карточка Telegram-канала */}
      <div
        style={{
          width: 880,
          backgroundColor: colors.card,
          borderRadius: 32,
          border: `2px solid ${colors.border}`,
          padding: 56,
          opacity: cardIn,
          transform: `translateY(${cardY}px)`,
          boxShadow: "0 40px 90px rgba(255,87,34,0.16)",
        }}
      >
        {/* Шапка канала: аватар + название */}
        <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
          <div
            style={{
              width: 120,
              height: 120,
              borderRadius: 60,
              flexShrink: 0,
              background: `linear-gradient(150deg, ${colors.accent}, ${colors.accentDim})`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: colors.text,
              fontSize: 56,
              fontWeight: 800,
            }}
          >
            Ю
          </div>
          <div>
            <div style={{ color: colors.text, fontSize: 40, fontWeight: 800, lineHeight: 1.2 }}>
              Юмсунов
              <br />
              Про реальный бизнес
            </div>
            <div
              style={{
                color: colors.textDim,
                fontFamily: jetBrainsMono,
                fontSize: 24,
                fontWeight: 400,
                marginTop: 10,
              }}
            >
              @yumsunov_realbiz
            </div>
          </div>
        </div>

        {/* Описание канала */}
        <div
          style={{
            color: colors.textDim,
            fontSize: 30,
            fontWeight: 500,
            lineHeight: 1.45,
            marginTop: 36,
          }}
        >
          Картинг и глэмпинг изнутри. Как устроен бизнес — без глянца, на своём опыте.
        </div>

        {/* Кнопка подписки */}
        <div
          style={{
            position: "relative",
            marginTop: 44,
            opacity: btnIn,
          }}
        >
          {/* «Тап» — расходящийся круг */}
          {tap > 0 && tap < 1 && (
            <div
              style={{
                position: "absolute",
                left: "50%",
                top: "50%",
                width: 80,
                height: 80,
                marginLeft: -40,
                marginTop: -40,
                borderRadius: "50%",
                border: `3px solid ${colors.accent}`,
                opacity: 1 - tap,
                transform: `scale(${1 + tap * 4})`,
              }}
            />
          )}
          <div
            style={{
              padding: "28px 0",
              borderRadius: 18,
              textAlign: "center",
              backgroundColor: subscribed ? colors.card : colors.accent,
              border: subscribed ? `2px solid ${colors.accent}` : "none",
              color: colors.text,
              fontSize: 34,
              fontWeight: 800,
              letterSpacing: 1,
              transform: `scale(${btnPulse * press})`,
            }}
          >
            {subscribed ? "✓ Вы подписаны" : "Подписаться"}
          </div>
        </div>
      </div>

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
          opacity: cardIn,
        }}
      >
        LIFE DRIVE
      </div>
    </AbsoluteFill>
  );
};
