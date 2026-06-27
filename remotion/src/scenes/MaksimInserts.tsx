/**
 * MaksimInserts — короткие B-roll-вставки (~4 сек) для про-монтажа.
 *
 * ВАЖНО про композицию кадра:
 * Кадр 1080×1920 (9:16), но ВЕСЬ экшен живёт в центральной полосе
 * 1080×960 — это band y∈[480,1440]. Именно эту полосу про-монтаж
 * вырезает (center-crop) для split-layout (верхний слот 1080×960).
 * Поэтому split-верх показывает контент 1:1, без растяжения и леттербокса.
 *
 * ВАЖНО про тайминг:
 * Сегмент про-монтажа ~3 сек. Поэтому каждая вставка должна выйти на
 * ПОЛНОЕ состояние за ~1 сек (≈frame 30) и держать его — иначе зритель
 * увидит только середину анимации. Все билды быстрые.
 *
 * 6 вставок, каждая 120 frames @ 30fps = 4 сек, 1080×1920.
 * Стиль: Постулат-dark (#0a0a0a + accent #ff5722 + Inter Tight).
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

type P = { [key: string]: unknown };
const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

// ── Геометрия центральной «квадратной» полосы ────────────────────────
const BAND_W = 1080;
const BAND_H = 960;
const BAND_TOP = (1920 - BAND_H) / 2; // 480

// Фон на весь кадр: dark + мягкое свечение по центру.
const Ambient: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: colors.bg, overflow: "hidden" }}>
    <div
      style={{
        position: "absolute",
        left: "50%",
        top: "50%",
        width: 1500,
        height: 1500,
        transform: "translate(-50%, -50%)",
        background: `radial-gradient(circle, ${colors.accent}26 0%, transparent 64%)`,
      }}
    />
  </AbsoluteFill>
);

// Центральная полоса-холст. Всё содержимое вставки кладётся сюда.
const Band: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      position: "absolute",
      top: BAND_TOP,
      left: 0,
      width: BAND_W,
      height: BAND_H,
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
    }}
  >
    {children}
  </div>
);

// Подпись-секция у верхнего края полосы.
const Label: React.FC<{ text: string }> = ({ text }) => (
  <div
    style={{
      position: "absolute",
      top: 30,
      left: 0,
      right: 0,
      textAlign: "center",
      color: colors.textDim,
      fontSize: 27,
      fontWeight: 700,
      letterSpacing: 5,
    }}
  >
    {text}
  </div>
);

// Контейнер для translate-центрированных элементов внутри полосы.
const Center: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
    }}
  >
    {children}
  </div>
);

// ── Вставка 1: хаос уведомлений ──────────────────────────────────────
const CHAOS_NOTIFS = [
  { text: "Где отчёт?", x: -210, y: -250, rot: -7, at: 2 },
  { text: "Срочно перезвони", x: 200, y: -110, rot: 6, at: 7 },
  { text: "Задача висит", x: -240, y: 35, rot: -5, at: 12 },
  { text: "Клиент ждёт", x: 215, y: 175, rot: 7, at: 17 },
  { text: "Согласуй смету", x: -160, y: 305, rot: -6, at: 22 },
];

export const InsertChaos: React.FC<P> = () => {
  const f = useCurrentFrame();
  const counter = Math.round(
    interpolate(f, [2, 46], [5, 44], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="НЕПРОЧИТАННОЕ" />
        <div
          style={{
            position: "absolute",
            top: 92,
            left: 0,
            right: 0,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: 18,
          }}
        >
          <span
            style={{
              color: colors.textDim,
              fontSize: 28,
              fontWeight: 600,
            }}
          >
            входящие
          </span>
          <div
            style={{
              padding: "10px 26px",
              borderRadius: 22,
              backgroundColor: colors.accent,
              color: colors.text,
              fontFamily: jetBrainsMono,
              fontSize: 36,
              fontWeight: 700,
            }}
          >
            {counter}
          </div>
        </div>
        <Center>
          {CHAOS_NOTIFS.map((n, i) => {
            const p = interpolate(f, [n.at, n.at + 9], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            if (p <= 0) return null;
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  transform: `translate(${n.x}px, ${n.y}px) rotate(${n.rot}deg) scale(${0.7 + 0.3 * ease(p)})`,
                  opacity: p,
                  padding: "22px 32px",
                  backgroundColor: colors.card,
                  border: `2px solid ${colors.accent}`,
                  borderRadius: 16,
                  color: colors.text,
                  fontSize: 35,
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                  boxShadow: "0 14px 38px rgba(0,0,0,0.5)",
                }}
              >
                {n.text}
              </div>
            );
          })}
        </Center>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 2: диктофон слушает планёрку ─────────────────────────────
export const InsertPlaud: React.FC<P> = () => {
  const f = useCurrentFrame();
  const appear = interpolate(f, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  const bars = Array.from({ length: 15 });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="ИИ-АССИСТЕНТ" />
        <div
          style={{
            opacity: appear,
            transform: `scale(${0.86 + 0.14 * ease(appear)})`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 54,
          }}
        >
          {/* Диктофон */}
          <div
            style={{
              width: 168,
              height: 168,
              borderRadius: 40,
              backgroundColor: colors.card,
              border: `3px solid ${colors.accent}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: `0 0 60px ${colors.accent}40`,
            }}
          >
            <div
              style={{
                width: 52,
                height: 52,
                borderRadius: 26,
                backgroundColor: colors.accent,
              }}
            />
          </div>
          {/* Звуковая волна */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              height: 150,
            }}
          >
            {bars.map((_, i) => {
              const h = 30 + 104 * Math.abs(Math.sin(f / 5 + i * 0.6));
              return (
                <div
                  key={i}
                  style={{
                    width: 15,
                    height: h,
                    borderRadius: 8,
                    backgroundColor: i % 2 ? colors.accent : colors.text,
                  }}
                />
              );
            })}
          </div>
          <div
            style={{
              color: colors.text,
              fontSize: 48,
              fontWeight: 800,
              textAlign: "center",
              maxWidth: 940,
            }}
          >
            Слушает планёрку
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 3: задачи прилетают к сотрудникам ────────────────────────
const FLY_TASKS = [
  { who: "А", txt: "Смета на трассу", at: 0 },
  { who: "Д", txt: "Бронь на выходные", at: 9 },
  { who: "Р", txt: "Закупка экипировки", at: 18 },
];
export const InsertTaskFly: React.FC<P> = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="ЗАДАЧИ КОМАНДЕ" />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 32,
            width: 952,
          }}
        >
          {FLY_TASKS.map((t, i) => {
            const p = interpolate(f, [t.at, t.at + 11], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            if (p <= 0) return null;
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 26,
                  padding: "30px 34px",
                  backgroundColor: colors.card,
                  border: `2px solid ${colors.border}`,
                  borderLeft: `6px solid ${colors.accent}`,
                  borderRadius: 20,
                  opacity: p,
                  transform: `translateX(${(1 - ease(p)) * -90}px)`,
                  boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
                }}
              >
                <div
                  style={{
                    width: 76,
                    height: 76,
                    borderRadius: 38,
                    flexShrink: 0,
                    backgroundColor: colors.bg,
                    border: `2px solid ${colors.accent}`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: colors.text,
                    fontSize: 34,
                    fontWeight: 700,
                  }}
                >
                  {t.who}
                </div>
                <span
                  style={{
                    color: colors.text,
                    fontSize: 38,
                    fontWeight: 600,
                  }}
                >
                  {t.txt}
                </span>
                <span
                  style={{
                    marginLeft: "auto",
                    color: colors.accent,
                    fontFamily: jetBrainsMono,
                    fontSize: 34,
                    fontWeight: 700,
                  }}
                >
                  →
                </span>
              </div>
            );
          })}
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 4: окно задач наполняется ────────────────────────────────
const BITRIX_ROWS = [
  "Смета на трассу",
  "Бронь — 6 домов",
  "Закупка экипировки",
  "Счёт поставщику",
];
export const InsertBitrix: React.FC<P> = () => {
  const f = useCurrentFrame();
  const win = interpolate(f, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <div
          style={{
            width: 944,
            backgroundColor: colors.card,
            border: `2px solid ${colors.border}`,
            borderRadius: 28,
            overflow: "hidden",
            opacity: win,
            transform: `translateY(${(1 - ease(win)) * 40}px)`,
            boxShadow: "0 18px 48px rgba(0,0,0,0.5)",
          }}
        >
          <div
            style={{
              padding: "28px 38px",
              backgroundColor: "#0f0f0f",
              borderBottom: `1px solid ${colors.border}`,
              display: "flex",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div
              style={{
                width: 18,
                height: 18,
                borderRadius: 5,
                backgroundColor: colors.accent,
              }}
            />
            <span
              style={{
                color: colors.text,
                fontSize: 34,
                fontWeight: 700,
              }}
            >
              Задачи в системе
            </span>
          </div>
          <div
            style={{
              padding: "28px 38px",
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            {BITRIX_ROWS.map((r, i) => {
              const p = interpolate(
                f,
                [10 + i * 6, 10 + i * 6 + 9],
                [0, 1],
                {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                },
              );
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 20,
                    padding: "22px 26px",
                    backgroundColor: colors.bg,
                    borderRadius: 16,
                    opacity: p,
                    transform: `translateX(${(1 - ease(p)) * -50}px)`,
                  }}
                >
                  <div
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: 20,
                      flexShrink: 0,
                      backgroundColor: colors.accent,
                      color: colors.text,
                      fontSize: 24,
                      fontWeight: 800,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    ✓
                  </div>
                  <span
                    style={{
                      color: colors.text,
                      fontSize: 34,
                      fontWeight: 600,
                    }}
                  >
                    {r}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 5: вечерний отчёт ────────────────────────────────────────
export const InsertReport: React.FC<P> = () => {
  const f = useCurrentFrame();
  const card = interpolate(f, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const bar = interpolate(f, [12, 44], [0, 0.75], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const done = Math.round(
    interpolate(f, [12, 44], [0, 6], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <div
          style={{
            width: 944,
            backgroundColor: colors.card,
            border: `2px solid ${colors.border}`,
            borderRadius: 28,
            padding: 52,
            opacity: card,
            transform: `translateY(${(1 - ease(card)) * 40}px)`,
            boxShadow: "0 18px 48px rgba(0,0,0,0.5)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 32,
            }}
          >
            <span
              style={{
                color: colors.textDim,
                fontFamily: jetBrainsMono,
                fontSize: 28,
                fontWeight: 600,
                letterSpacing: 2,
              }}
            >
              ОТЧЁТ ДНЯ · 21:00
            </span>
            <span
              style={{
                color: colors.text,
                fontSize: 46,
                fontWeight: 800,
              }}
            >
              {done} из 8
            </span>
          </div>
          <div
            style={{
              width: "100%",
              height: 26,
              borderRadius: 13,
              backgroundColor: colors.border,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${bar * 100}%`,
                height: "100%",
                borderRadius: 13,
                backgroundColor: colors.accent,
              }}
            />
          </div>
          <div
            style={{
              marginTop: 36,
              display: "flex",
              gap: 20,
            }}
          >
            {[
              { n: "6", t: "сделано" },
              { n: "2", t: "висит" },
              { n: "0", t: "забыто" },
            ].map((s, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  backgroundColor: colors.bg,
                  borderRadius: 18,
                  padding: "24px 18px",
                  textAlign: "center",
                }}
              >
                <div
                  style={{
                    color: i === 0 ? colors.accent : colors.text,
                    fontSize: 52,
                    fontWeight: 800,
                  }}
                >
                  {s.n}
                </div>
                <div
                  style={{
                    color: colors.textDim,
                    fontSize: 26,
                    fontWeight: 500,
                    marginTop: 4,
                  }}
                >
                  {s.t}
                </div>
              </div>
            ))}
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 6: голова свободна ───────────────────────────────────────
export const InsertFreed: React.FC<P> = () => {
  const f = useCurrentFrame();
  const scatter = interpolate(f, [6, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const textIn = interpolate(f, [20, 42], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const icons = [
    { x: -210, y: -120 },
    { x: 190, y: -90 },
    { x: -160, y: 60 },
    { x: 215, y: 85 },
    { x: -60, y: 175 },
    { x: 120, y: -200 },
  ];
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Center>
          {icons.map((ic, i) => {
            const fly = ease(scatter);
            const dx = ic.x * (1 + fly * 2.4);
            const dy = ic.y * (1 + fly * 2.4);
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  transform: `translate(${dx}px, ${dy}px)`,
                  opacity: (1 - scatter) * 0.9,
                  width: 72,
                  height: 72,
                  borderRadius: 18,
                  backgroundColor: colors.card,
                  border: `2px solid ${colors.accent}`,
                }}
              />
            );
          })}
        </Center>
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            textAlign: "center",
            opacity: textIn,
            transform: `scale(${0.9 + 0.1 * ease(textIn)})`,
            padding: "0 80px",
          }}
        >
          <div
            style={{
              color: colors.text,
              fontSize: 68,
              fontWeight: 800,
              lineHeight: 1.2,
            }}
          >
            Голова свободна
            <br />
            <span style={{ color: colors.accent }}>для решений</span>
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};
