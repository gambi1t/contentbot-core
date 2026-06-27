/**
 * MaksimTasksBroll — демо B-roll сцена для контент-бота Максима (Life Drive).
 *
 * Визуализирует механику из сценария ролика: «ассистент слушает планёрку,
 * сам ставит задачи сотрудникам, вечером присылает отчёт». То, что не снять
 * камерой — показываем motion-графикой: fake-окно task-менеджера, где задачи
 * сами разлетаются по людям, затем всплывает отчёт дня.
 *
 * 8 сек, 240 frames @ 30fps, 1080×1920 9:16.
 *   0.0–0.9с (0-26)    — fade-in окно «Задачи команды»
 *   0.9–4.8с (26-145)  — 4 задачи прилетают по очереди к сотрудникам
 *   5.0–7.0с (150-210) — снизу всплывает блок «Отчёт дня» + прогресс-бар
 *   7.0–8.0с (210-240) — hold + бренд-марка
 *
 * Стиль: Постулат-dark (#0a0a0a + accent #ff5722 + Inter Tight).
 * B-roll вставка в ролик Максима.
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

export type MaksimTasksBrollProps = {
  // Index signature нужен для Remotion <Composition> generic constraint
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

type Task = {
  text: string;
  person: string;
  initial: string;
};

const TASKS: Task[] = [
  { text: "Смета на ремонт трассы", person: "Алексей · инженер", initial: "А" },
  { text: "Бронь на выходные — 6 домов", person: "Дарья · администратор", initial: "Д" },
  { text: "Закупка новой экипировки", person: "Руслан · механик", initial: "Р" },
  { text: "Счёт поставщику пеллет", person: "Марина · бухгалтер", initial: "М" },
];

// Кадр появления каждой задачи
const FIRST_TASK_FRAME = 30;
const TASK_STEP = 26;
const REPORT_FRAME = 150;

export const MaksimTasksBroll: React.FC<MaksimTasksBrollProps> = () => {
  const frame = useCurrentFrame();

  // Phase 1: окно fade-in (0-26)
  const winOpacity = interpolate(frame, [0, 26], [0, 1], {
    extrapolateRight: "clamp",
  });
  const winY = interpolate(frame, [0, 26], [50, 0], {
    extrapolateRight: "clamp",
  });

  // Пульс статус-точки «планёрка идёт»
  const pulse = 0.45 + 0.55 * (0.5 + 0.5 * Math.sin(frame / 6));

  // Все задачи распределены — после появления последней
  const allTasksFrame = FIRST_TASK_FRAME + TASKS.length * TASK_STEP;
  const distributed = frame > allTasksFrame;

  // Phase 3: блок отчёта (150-210)
  const reportProgress = interpolate(frame, [REPORT_FRAME, REPORT_FRAME + 22], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const reportShown = frame >= REPORT_FRAME;
  // Прогресс-бар отчёта: 6 из 8 = 75%
  const barFill = interpolate(frame, [REPORT_FRAME + 18, REPORT_FRAME + 55], [0, 0.75], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const doneCount = Math.round(
    interpolate(frame, [REPORT_FRAME + 18, REPORT_FRAME + 55], [0, 6], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );

  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.bg,
        fontFamily: interTight,
        padding: 60,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {/* Окно task-менеджера */}
      <div
        style={{
          width: 960,
          backgroundColor: colors.card,
          borderRadius: 28,
          border: `2px solid ${colors.border}`,
          overflow: "hidden",
          opacity: winOpacity,
          transform: `translateY(${winY}px)`,
          boxShadow: "0 40px 90px rgba(255,87,34,0.16)",
        }}
      >
        {/* Шапка окна */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "30px 38px",
            backgroundColor: "#0f0f0f",
            borderBottom: `1px solid ${colors.border}`,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div
              style={{
                width: 18,
                height: 18,
                borderRadius: 5,
                backgroundColor: colors.accent,
              }}
            />
            <span style={{ color: colors.text, fontSize: 34, fontWeight: 700 }}>
              Задачи команды
            </span>
          </div>
          <span
            style={{
              color: colors.textDim,
              fontFamily: jetBrainsMono,
              fontSize: 26,
              fontWeight: 400,
            }}
          >
            09:14
          </span>
        </div>

        {/* Статус-строка */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "22px 38px",
            borderBottom: `1px solid ${colors.border}`,
          }}
        >
          <div
            style={{
              width: 14,
              height: 14,
              borderRadius: 7,
              backgroundColor: distributed ? "#27c93f" : colors.accent,
              opacity: distributed ? 1 : pulse,
            }}
          />
          <span
            style={{
              color: distributed ? "#27c93f" : colors.textDim,
              fontSize: 25,
              fontWeight: 600,
            }}
          >
            {distributed
              ? "Задачи распределены ассистентом"
              : "Планёрка идёт — ассистент слушает"}
          </span>
        </div>

        {/* Список задач */}
        <div style={{ padding: "32px 38px", display: "flex", flexDirection: "column", gap: 20 }}>
          {TASKS.map((task, i) => {
            const appear = FIRST_TASK_FRAME + i * TASK_STEP;
            const p = interpolate(frame, [appear, appear + 16], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            if (p <= 0) return null;
            const slide = (1 - ease(p)) * -55;
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 22,
                  padding: "24px 28px",
                  backgroundColor: colors.bg,
                  borderRadius: 18,
                  borderLeft: `4px solid ${colors.accent}`,
                  opacity: p,
                  transform: `translateX(${slide}px)`,
                }}
              >
                {/* Аватар сотрудника */}
                <div
                  style={{
                    width: 64,
                    height: 64,
                    borderRadius: 32,
                    flexShrink: 0,
                    backgroundColor: colors.card,
                    border: `2px solid ${colors.accent}`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: colors.text,
                    fontSize: 30,
                    fontWeight: 700,
                  }}
                >
                  {task.initial}
                </div>
                {/* Текст задачи */}
                <div style={{ flex: 1 }}>
                  <div style={{ color: colors.text, fontSize: 31, fontWeight: 600 }}>
                    {task.text}
                  </div>
                  <div
                    style={{
                      color: colors.textDim,
                      fontSize: 22,
                      fontWeight: 400,
                      marginTop: 6,
                    }}
                  >
                    {task.person}
                  </div>
                </div>
                {/* Значок «поставлено» */}
                <div
                  style={{
                    color: colors.accent,
                    fontFamily: jetBrainsMono,
                    fontSize: 24,
                    fontWeight: 700,
                  }}
                >
                  →
                </div>
              </div>
            );
          })}
        </div>

        {/* Блок «Отчёт дня» */}
        {reportShown && (
          <div
            style={{
              margin: "0 38px 36px",
              padding: "28px 32px",
              backgroundColor: "#0f0f0f",
              borderRadius: 20,
              border: `1px solid ${colors.border}`,
              opacity: reportProgress,
              transform: `translateY(${(1 - ease(reportProgress)) * 40}px)`,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 18,
              }}
            >
              <span
                style={{
                  color: colors.textDim,
                  fontFamily: jetBrainsMono,
                  fontSize: 22,
                  fontWeight: 600,
                  letterSpacing: 2,
                }}
              >
                ОТЧЁТ ДНЯ · 21:00
              </span>
              <span style={{ color: colors.text, fontSize: 28, fontWeight: 700 }}>
                {doneCount} из 8
              </span>
            </div>
            {/* Прогресс-бар */}
            <div
              style={{
                width: "100%",
                height: 16,
                borderRadius: 8,
                backgroundColor: colors.border,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${barFill * 100}%`,
                  height: "100%",
                  borderRadius: 8,
                  backgroundColor: colors.accent,
                }}
              />
            </div>
            <div
              style={{
                marginTop: 16,
                color: colors.textDim,
                fontSize: 24,
                fontWeight: 500,
              }}
            >
              Сделано 6 · В работе 2 — без единого напоминания вручную
            </div>
          </div>
        )}
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
          opacity: winOpacity,
        }}
      >
        LIFE DRIVE
      </div>
    </AbsoluteFill>
  );
};
