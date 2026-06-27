/**
 * MaksimInserts2 — B-roll-вставки для ролика #2 «Дорогие пустые часы».
 * Тема: загрузка по дням недели, простой в будни.
 *
 * Те же правила, что в MaksimInserts.tsx:
 *  - весь экшен в центральной полосе 1080×960 (band y∈[480,1440]);
 *  - полный визуал за ~1 сек, дальше держим;
 *  - стиль Постулат-dark (#0a0a0a + accent #ff5722 + Inter Tight).
 *
 * Сквозной мотив — недельная диаграмма (вставки 1 → 6: проблема → итог).
 * 6 вставок, каждая 120 frames @ 30fps = 4 сек, 1080×1920.
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

type P = { [key: string]: unknown };
const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

// ── Геометрия центральной полосы ─────────────────────────────────────
const BAND_W = 1080;
const BAND_H = 960;
const BAND_TOP = (1920 - BAND_H) / 2; // 480
const DIM = "#3a3a3a";

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

// ── Недельная диаграмма (мотив вставок 1 и 6) ────────────────────────
const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const CHART_MAX_H = 372;

const WeekChart: React.FC<{ loads: number[]; weekdayAccent: boolean }> = ({
  loads,
  weekdayAccent,
}) => {
  const f = useCurrentFrame();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        gap: 26,
        height: CHART_MAX_H + 60,
      }}
    >
      {loads.map((load, i) => {
        const isWeekend = i >= 5;
        const accent = isWeekend || weekdayAccent;
        const grow = interpolate(f, [4 + i * 2, 4 + i * 2 + 16], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const h = Math.max((load / 100) * CHART_MAX_H * ease(grow), 8);
        return (
          <div
            key={i}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div
              style={{
                width: 94,
                height: h,
                borderRadius: 14,
                backgroundColor: accent ? colors.accent : DIM,
                boxShadow: accent ? `0 0 30px ${colors.accent}55` : "none",
              }}
            />
            <span
              style={{
                color: accent ? colors.text : colors.textDim,
                fontSize: 30,
                fontWeight: 700,
              }}
            >
              {DAYS[i]}
            </span>
          </div>
        );
      })}
    </div>
  );
};

// ── Вставка 1: загрузка по дням — будни пустые ───────────────────────
export const InsertWeekLoad: React.FC<P> = () => {
  const f = useCurrentFrame();
  const capIn = interpolate(f, [24, 38], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="ЗАГРУЗКА ПО ДНЯМ" />
        <WeekChart
          loads={[18, 12, 22, 16, 30, 95, 90]}
          weekdayAccent={false}
        />
        <div
          style={{
            marginTop: 44,
            display: "flex",
            gap: 90,
            opacity: capIn,
          }}
        >
          <span style={{ color: colors.textDim, fontSize: 32, fontWeight: 600 }}>
            будни — простой
          </span>
          <span style={{ color: colors.accent, fontSize: 32, fontWeight: 700 }}>
            выходные — очередь
          </span>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 2: расходы идут все 7 дней ───────────────────────────────
const COSTS = ["Аренда", "Зарплаты", "Свет"];
export const InsertCosts7: React.FC<P> = () => {
  const f = useCurrentFrame();
  const card = interpolate(f, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="РАСХОДЫ ИДУТ 7 ДНЕЙ В НЕДЕЛЮ" />
        <div
          style={{
            width: 936,
            backgroundColor: colors.card,
            border: `2px solid ${colors.border}`,
            borderRadius: 28,
            padding: "44px 46px",
            display: "flex",
            flexDirection: "column",
            gap: 30,
            opacity: card,
            transform: `translateY(${(1 - ease(card)) * 40}px)`,
            boxShadow: "0 18px 48px rgba(0,0,0,0.5)",
          }}
        >
          {COSTS.map((cost, ci) => (
            <div
              key={ci}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 26,
              }}
            >
              <span
                style={{
                  width: 250,
                  color: colors.text,
                  fontSize: 38,
                  fontWeight: 700,
                }}
              >
                {cost}
              </span>
              <div style={{ display: "flex", gap: 14 }}>
                {DAYS.map((d, di) => {
                  const lit = interpolate(
                    f,
                    [12 + (ci * 7 + di) * 2, 12 + (ci * 7 + di) * 2 + 8],
                    [0, 1],
                    {
                      extrapolateLeft: "clamp",
                      extrapolateRight: "clamp",
                    },
                  );
                  return (
                    <div
                      key={di}
                      style={{
                        width: 58,
                        height: 58,
                        borderRadius: 12,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        backgroundColor:
                          lit > 0.5 ? colors.accent : colors.bg,
                        border: `2px solid ${lit > 0.5 ? colors.accent : colors.border}`,
                        color: colors.text,
                        fontSize: 22,
                        fontWeight: 700,
                        opacity: 0.4 + 0.6 * lit,
                      }}
                    >
                      {d}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 3: пустой будний день — это минус ────────────────────────
export const InsertEmptyDay: React.FC<P> = () => {
  const f = useCurrentFrame();
  const card = interpolate(f, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  const minusIn = interpolate(f, [20, 36], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <div
          style={{
            width: 700,
            backgroundColor: colors.card,
            border: `2px solid ${colors.border}`,
            borderRadius: 32,
            padding: "52px 56px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            opacity: card,
            transform: `scale(${0.88 + 0.12 * ease(card)})`,
            boxShadow: "0 18px 48px rgba(0,0,0,0.5)",
          }}
        >
          <span
            style={{
              color: colors.textDim,
              fontFamily: jetBrainsMono,
              fontSize: 30,
              fontWeight: 600,
              letterSpacing: 3,
            }}
          >
            ВТОРНИК · БУДНИЙ ДЕНЬ
          </span>
          <div
            style={{
              color: colors.text,
              fontSize: 230,
              fontWeight: 800,
              lineHeight: 1,
              marginTop: 18,
            }}
          >
            0
          </div>
          <span
            style={{
              color: colors.textDim,
              fontSize: 38,
              fontWeight: 600,
              marginBottom: 34,
            }}
          >
            заездов на трассе
          </span>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 16,
              padding: "18px 36px",
              borderRadius: 18,
              backgroundColor: colors.accent,
              opacity: minusIn,
              transform: `translateY(${(1 - ease(minusIn)) * 26}px)`,
            }}
          >
            <span style={{ color: colors.text, fontSize: 46, fontWeight: 800 }}>
              ↓
            </span>
            <span style={{ color: colors.text, fontSize: 38, fontWeight: 700 }}>
              а расходы идут
            </span>
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 4: я считал не ту метрику ────────────────────────────────
export const InsertWrongMetric: React.FC<P> = () => {
  const f = useCurrentFrame();
  const lIn = interpolate(f, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  const rIn = interpolate(f, [8, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // после frame 28 — левая метрика гаснет, правая разгорается
  const swap = interpolate(f, [28, 44], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rHot = swap > 0.45;
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="Я СЧИТАЛ НЕ ТО" />
        <div style={{ display: "flex", gap: 40, alignItems: "stretch" }}>
          {/* Левая — неверная метрика */}
          <div
            style={{
              width: 452,
              minHeight: 396,
              boxSizing: "border-box",
              backgroundColor: colors.card,
              border: `2px solid ${colors.border}`,
              borderRadius: 28,
              padding: "48px 44px",
              display: "flex",
              flexDirection: "column",
              opacity: lIn * (1 - 0.45 * swap),
              position: "relative",
              boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
            }}
          >
            <span style={{ color: colors.textDim, fontSize: 32, fontWeight: 600 }}>
              Выручка выходных
            </span>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 14,
                marginTop: 28,
                marginBottom: 22,
                position: "relative",
              }}
            >
              <span style={{ color: colors.text, fontSize: 68, fontWeight: 800 }}>
                ↑
              </span>
              <span style={{ color: colors.text, fontSize: 52, fontWeight: 800 }}>
                растёт
              </span>
              {/* перечёркивание поверх значения */}
              <div
                style={{
                  position: "absolute",
                  left: -6,
                  right: -6,
                  top: "52%",
                  height: 7,
                  borderRadius: 4,
                  backgroundColor: colors.accent,
                  transform: `scaleX(${ease(swap)})`,
                  transformOrigin: "left",
                }}
              />
            </div>
            <span
              style={{
                color: colors.textDim,
                fontSize: 28,
                fontWeight: 500,
                marginTop: "auto",
              }}
            >
              видно только пик Сб–Вс
            </span>
          </div>
          {/* Правая — верная метрика */}
          <div
            style={{
              width: 452,
              minHeight: 396,
              boxSizing: "border-box",
              backgroundColor: colors.card,
              border: `3px solid ${rHot ? colors.accent : colors.border}`,
              borderRadius: 28,
              padding: "48px 44px",
              display: "flex",
              flexDirection: "column",
              opacity: rIn,
              boxShadow: rHot
                ? `0 0 56px ${colors.accent}55`
                : "0 12px 32px rgba(0,0,0,0.45)",
            }}
          >
            <span
              style={{
                color: rHot ? colors.accent : colors.textDim,
                fontSize: 32,
                fontWeight: 700,
              }}
            >
              Загрузка за неделю
            </span>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 14,
                marginTop: 28,
                marginBottom: 22,
              }}
            >
              <span style={{ color: colors.text, fontSize: 80, fontWeight: 800 }}>
                7/7
              </span>
              <span style={{ color: colors.text, fontSize: 40, fontWeight: 700 }}>
                дней
              </span>
            </div>
            <span
              style={{
                color: colors.textDim,
                fontSize: 28,
                fontWeight: 500,
                marginTop: "auto",
              }}
            >
              видно весь простой
            </span>
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 5: занялся буднями ───────────────────────────────────────
const BUDNI = [
  { txt: "Корпоративы", day: "Чт", at: 0 },
  { txt: "Дневной тариф", day: "Вт", at: 9 },
  { txt: "Автошколы", day: "Ср", at: 18 },
];
export const InsertFillBudni: React.FC<P> = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="ЗАНЯЛСЯ БУДНЯМИ" />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 30,
            width: 940,
          }}
        >
          {BUDNI.map((b, i) => {
            const p = interpolate(f, [b.at, b.at + 12], [0, 1], {
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
                  padding: "32px 36px",
                  backgroundColor: colors.card,
                  border: `2px solid ${colors.border}`,
                  borderLeft: `6px solid ${colors.accent}`,
                  borderRadius: 20,
                  opacity: p,
                  transform: `translateX(${(1 - ease(p)) * -100}px)`,
                  boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
                }}
              >
                <span
                  style={{
                    color: colors.text,
                    fontSize: 40,
                    fontWeight: 700,
                  }}
                >
                  {b.txt}
                </span>
                <div
                  style={{
                    marginLeft: "auto",
                    display: "flex",
                    alignItems: "center",
                    gap: 14,
                  }}
                >
                  <span
                    style={{
                      color: colors.accent,
                      fontFamily: jetBrainsMono,
                      fontSize: 32,
                      fontWeight: 700,
                    }}
                  >
                    →
                  </span>
                  <div
                    style={{
                      width: 70,
                      height: 70,
                      borderRadius: 16,
                      backgroundColor: colors.accent,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: colors.text,
                      fontSize: 30,
                      fontWeight: 800,
                    }}
                  >
                    {b.day}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Band>
    </AbsoluteFill>
  );
};

// ── Вставка 6: неделя закрыта — загрузка выровнялась ─────────────────
export const InsertWeekFull: React.FC<P> = () => {
  const f = useCurrentFrame();
  const capIn = interpolate(f, [26, 40], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="ЗАГРУЗКА ПО ДНЯМ" />
        <WeekChart
          loads={[70, 66, 74, 70, 82, 96, 92]}
          weekdayAccent={true}
        />
        <div
          style={{
            marginTop: 44,
            display: "flex",
            alignItems: "center",
            gap: 16,
            opacity: capIn,
          }}
        >
          <span style={{ color: colors.accent, fontSize: 36, fontWeight: 800 }}>
            ✓
          </span>
          <span style={{ color: colors.text, fontSize: 34, fontWeight: 700 }}>
            расписание закрылось
          </span>
        </div>
      </Band>
    </AbsoluteFill>
  );
};
