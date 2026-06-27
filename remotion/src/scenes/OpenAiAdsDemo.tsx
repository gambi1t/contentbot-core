/**
 * OpenAiAdsDemo — B-roll для новости «OpenAI открыл рекламу в ChatGPT для малого бизнеса».
 *
 * 18 сек / 540 frames @ 30fps / 1080×1920 9:16
 *
 * Хронометраж:
 *   0.0–3.0с  (frames   0–90)  Сцена 1: ChatGPT-окно, юзер спрашивает «что купить?»
 *   3.0–5.0с  (frames  90–150) Появляется ответ + Sponsored-label рядом мигает
 *   5.0–6.0с  (frames 150–180) Zoom-переход к интерфейсу Ads Manager
 *   6.0–12.0с (frames 180–360) Ads Manager UI: sidebar, форма campaign, ввод бюджета
 *   12.0–15.0с (frames 360–450) Нажатие "Launch Campaign" → toast "Campaign live"
 *   15.0–18.0с (frames 450–540) Финал-шутка: "лучший" vs "оплаченный" в чате
 */
import { AbsoluteFill, interpolate, useCurrentFrame, Sequence } from "remotion";
import { interTight, jetBrainsMono, colors } from "../fonts";

export type OpenAiAdsDemoProps = {
  // Опциональные параметры если захотим переиспользовать сцену
  newsDate?: string;
  [key: string]: unknown;
};

const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

// =========================================================================
// PHASE 1+2: ChatGPT окно с вопросом юзера и ответом + sponsored-метка
// =========================================================================
const ChatScene: React.FC = () => {
  const frame = useCurrentFrame(); // local frame within Sequence (0..150)

  // Window fade-in (0-15)
  const windowOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const windowY = interpolate(frame, [0, 15], [30, 0], { extrapolateRight: "clamp" });

  // User question typewriter (15-75)
  const question = "что купить ребёнку на день рождения?";
  const qChars = Math.floor(interpolate(frame, [15, 75], [0, question.length], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  }));

  // Response appears (90-150)
  const responseOpacity = interpolate(frame, [90, 110], [0, 1], { extrapolateRight: "clamp" });

  // Sponsored badge pulse (110+)
  const sponsoredPulse = frame > 110
    ? 0.5 + 0.5 * Math.sin((frame - 110) * 0.3)
    : 0;

  return (
    <AbsoluteFill style={{ backgroundColor: colors.bg, padding: 60, justifyContent: "center" }}>
      {/* ChatGPT-style window */}
      <div style={{
        backgroundColor: colors.card,
        borderRadius: 24,
        border: `2px solid ${colors.border}`,
        opacity: windowOpacity,
        transform: `translateY(${windowY}px)`,
        boxShadow: "0 40px 80px rgba(255,87,34,0.2)",
        overflow: "hidden",
      }}>
        {/* Window header */}
        <div style={{
          padding: "20px 28px",
          backgroundColor: "#0f0f0f",
          borderBottom: `1px solid ${colors.border}`,
          display: "flex", alignItems: "center", gap: 14,
        }}>
          <div style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: "#ff5f56" }} />
          <div style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: "#ffbd2e" }} />
          <div style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: "#27c93f" }} />
          <div style={{
            marginLeft: 20, color: colors.textDim,
            fontFamily: jetBrainsMono, fontSize: 22,
          }}>chat.openai.com</div>
        </div>

        {/* User message */}
        <div style={{ padding: 40 }}>
          <div style={{
            fontSize: 18, color: colors.textDim, marginBottom: 12,
            letterSpacing: 2, fontWeight: 600,
          }}>ВЫ</div>
          <div style={{
            padding: "20px 28px",
            backgroundColor: colors.bg,
            borderRadius: 16,
            color: colors.text, fontSize: 30, lineHeight: 1.4,
            minHeight: 60,
          }}>
            {question.slice(0, qChars)}
            {qChars < question.length && Math.floor(frame / 8) % 2 === 0 && (
              <span style={{ color: colors.accent }}>▊</span>
            )}
          </div>

          {/* ChatGPT response */}
          <div style={{ marginTop: 32, opacity: responseOpacity }}>
            <div style={{
              fontSize: 18, color: colors.accent, marginBottom: 12,
              letterSpacing: 2, fontWeight: 700,
            }}>CHATGPT</div>
            <div style={{
              padding: "20px 28px",
              color: colors.text, fontSize: 28, lineHeight: 1.5,
            }}>
              Конструктор LEGO Technic, набор для опытов или беспроводные наушники.
            </div>

            {/* Sponsored ad block (THE point of the news) */}
            <div style={{
              marginTop: 20,
              padding: "20px 28px",
              backgroundColor: "rgba(255,87,34,0.08)",
              border: `2px solid ${colors.accent}`,
              borderRadius: 16,
              opacity: responseOpacity,
            }}>
              <div style={{
                display: "inline-block",
                padding: "4px 12px",
                marginBottom: 12,
                backgroundColor: colors.accent,
                color: colors.text,
                borderRadius: 6,
                fontSize: 16,
                fontWeight: 700,
                letterSpacing: 1.5,
                opacity: 0.6 + 0.4 * sponsoredPulse,
              }}>SPONSORED</div>
              <div style={{ color: colors.text, fontSize: 26, lineHeight: 1.4 }}>
                LEGO Technic Bugatti — премиум-конструктор от 8 лет
              </div>
              <div style={{ color: colors.textDim, fontSize: 22, marginTop: 6 }}>
                ozon.ru · 12 990 ₽
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Caption */}
      <div style={{
        marginTop: 40, textAlign: "center",
        color: colors.text, fontSize: 32, fontWeight: 700,
        opacity: responseOpacity,
        fontFamily: interTight,
      }}>
        ChatGPT теперь — рекламная витрина
      </div>
    </AbsoluteFill>
  );
};

// =========================================================================
// PHASE 3+4: Ads Manager UI — sidebar + форма campaign
// =========================================================================
const AdsManagerScene: React.FC = () => {
  const frame = useCurrentFrame(); // 0..210 within sequence

  // Zoom-in transition (0-30)
  const scale = interpolate(frame, [0, 30], [1.15, 1.0], {
    extrapolateRight: "clamp",
    easing: (t) => ease(t, 2),
  });
  const opacity = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });

  // Sidebar items appear sequentially (30-60)
  const sidebarItems = [
    { icon: "📊", label: "Campaigns", active: true },
    { icon: "🎨", label: "Creatives", active: false },
    { icon: "👥", label: "Audiences", active: false },
    { icon: "📈", label: "Analytics", active: false },
    { icon: "🎯", label: "Pixel", active: false },
  ];

  // Form fields appear (60-150)
  const showName = frame > 60;
  const showBudget = frame > 90;
  const showBid = frame > 120;
  const showKeywords = frame > 150;

  // Budget value typewriter (90-130)
  const budgetTarget = "$5,000";
  const budgetChars = Math.floor(interpolate(frame, [90, 130], [0, budgetTarget.length], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  }));

  // CPC bid value typewriter (120-150)
  const bidTarget = "$0.42";
  const bidChars = Math.floor(interpolate(frame, [120, 150], [0, bidTarget.length], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  }));

  // Keywords appear (150-180)
  const keywordOpacity = (delay: number) => clamp01((frame - 150 - delay) / 8);

  // Launch button hover/click (180-210)
  const buttonScale =
    frame >= 195 && frame <= 201 ? 0.95 :
    frame > 201 && frame <= 210 ? 1.0 :
    frame >= 180 ? 1.0 : 1.0;
  const buttonGlow = frame >= 180 ? Math.min(1, (frame - 180) / 15) : 0;

  return (
    <AbsoluteFill style={{
      backgroundColor: colors.bg,
      transform: `scale(${scale})`,
      opacity,
    }}>
      {/* Browser chrome */}
      <div style={{
        height: 80, backgroundColor: "#0f0f0f",
        borderBottom: `1px solid ${colors.border}`,
        display: "flex", alignItems: "center", padding: "0 30px", gap: 12,
      }}>
        <div style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: "#ff5f56" }} />
        <div style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: "#ffbd2e" }} />
        <div style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: "#27c93f" }} />
        <div style={{
          marginLeft: 24, padding: "10px 20px",
          backgroundColor: "#1a1a1a", borderRadius: 10,
          color: colors.textDim, fontFamily: jetBrainsMono, fontSize: 20,
        }}>
          <span style={{ color: colors.accent }}>https://</span>ads.openai.com/manager
        </div>
      </div>

      {/* Main app: sidebar + content */}
      <div style={{ flex: 1, display: "flex", height: "100%" }}>
        {/* Sidebar */}
        <div style={{
          width: 280, backgroundColor: "#0f0f0f",
          borderRight: `1px solid ${colors.border}`,
          padding: "32px 16px", display: "flex", flexDirection: "column", gap: 4,
        }}>
          <div style={{
            padding: "0 16px 28px 16px",
            color: colors.accent, fontSize: 22, fontWeight: 800, letterSpacing: 1,
            fontFamily: interTight,
          }}>OpenAI Ads</div>
          {sidebarItems.map((item, i) => (
            <div key={item.label} style={{
              display: "flex", alignItems: "center", gap: 14,
              padding: "14px 16px", borderRadius: 10,
              backgroundColor: item.active ? "rgba(255,87,34,0.15)" : "transparent",
              borderLeft: item.active ? `3px solid ${colors.accent}` : "3px solid transparent",
              color: item.active ? colors.text : colors.textDim,
              fontSize: 22, fontWeight: item.active ? 700 : 500,
              fontFamily: interTight,
              opacity: clamp01((frame - 30 - i * 5) / 10),
            }}>
              <span style={{ fontSize: 24 }}>{item.icon}</span>
              {item.label}
            </div>
          ))}
        </div>

        {/* Form area */}
        <div style={{ flex: 1, padding: "40px 50px", overflow: "hidden" }}>
          <div style={{
            color: colors.text, fontSize: 36, fontWeight: 800, marginBottom: 8,
            fontFamily: interTight,
            opacity: clamp01((frame - 30) / 15),
          }}>
            New Campaign
          </div>
          <div style={{
            color: colors.textDim, fontSize: 20, marginBottom: 36,
            opacity: clamp01((frame - 30) / 15),
          }}>
            Запусти рекламу в ChatGPT за 5 минут
          </div>

          {/* Field: Campaign name */}
          {showName && (
            <FormField
              label="Название кампании"
              value="Бренд X · Запуск Q3"
              opacity={clamp01((frame - 60) / 12)}
            />
          )}

          {/* Field: Daily budget */}
          {showBudget && (
            <FormField
              label="Дневной бюджет"
              value={budgetTarget.slice(0, budgetChars) + (budgetChars < budgetTarget.length ? "▊" : "")}
              opacity={clamp01((frame - 90) / 12)}
              prefix=""
            />
          )}

          {/* Field: CPC bid */}
          {showBid && (
            <FormField
              label="CPC ставка"
              value={bidTarget.slice(0, bidChars) + (bidChars < bidTarget.length ? "▊" : "")}
              opacity={clamp01((frame - 120) / 12)}
            />
          )}

          {/* Keywords chips */}
          {showKeywords && (
            <div style={{ marginBottom: 24 }}>
              <div style={{
                color: colors.textDim, fontSize: 18, marginBottom: 10,
                letterSpacing: 1, fontWeight: 600,
              }}>КЛЮЧЕВЫЕ ЗАПРОСЫ</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                {["детям", "подарки", "конструктор", "развивающие игры"].map((kw, i) => (
                  <div key={kw} style={{
                    padding: "10px 16px",
                    backgroundColor: "rgba(255,87,34,0.12)",
                    color: colors.accent,
                    border: `1px solid ${colors.accent}`,
                    borderRadius: 20,
                    fontSize: 22,
                    fontFamily: interTight,
                    fontWeight: 600,
                    opacity: keywordOpacity(i * 4),
                    transform: `translateY(${(1 - keywordOpacity(i * 4)) * 8}px)`,
                  }}>{kw}</div>
                ))}
              </div>
            </div>
          )}

          {/* Launch button */}
          {frame >= 180 && (
            <div style={{ marginTop: 40 }}>
              <button style={{
                padding: "20px 48px",
                backgroundColor: colors.accent,
                color: colors.text,
                border: "none",
                borderRadius: 14,
                fontSize: 28,
                fontWeight: 800,
                fontFamily: interTight,
                letterSpacing: 1,
                cursor: "pointer",
                transform: `scale(${buttonScale})`,
                boxShadow: `0 0 ${buttonGlow * 60}px rgba(255,87,34,${buttonGlow * 0.6})`,
                transition: "none",
              }}>
                🚀 Launch Campaign
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Toast notification после нажатия */}
      {frame >= 200 && (
        <div style={{
          position: "absolute",
          bottom: 50,
          right: 50,
          padding: "20px 28px",
          backgroundColor: colors.card,
          borderLeft: `4px solid #27c93f`,
          borderRadius: 12,
          boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
          display: "flex", alignItems: "center", gap: 16,
          opacity: clamp01((frame - 200) / 10),
          transform: `translateX(${(1 - clamp01((frame - 200) / 12)) * 100}px)`,
        }}>
          <div style={{ fontSize: 36 }}>✅</div>
          <div>
            <div style={{
              color: colors.text, fontSize: 22, fontWeight: 700,
              fontFamily: interTight,
            }}>Campaign live</div>
            <div style={{ color: colors.textDim, fontSize: 18 }}>Реклама уже крутится в ChatGPT</div>
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};

// Helper: form field with label + input value
const FormField: React.FC<{
  label: string;
  value: string;
  opacity: number;
  prefix?: string;
}> = ({ label, value, opacity }) => (
  <div style={{
    marginBottom: 24,
    opacity,
    transform: `translateY(${(1 - opacity) * 12}px)`,
  }}>
    <div style={{
      color: colors.textDim, fontSize: 18, marginBottom: 8,
      letterSpacing: 1, fontWeight: 600,
    }}>{label.toUpperCase()}</div>
    <div style={{
      padding: "16px 20px",
      backgroundColor: "#0f0f0f",
      border: `1px solid ${colors.border}`,
      borderRadius: 10,
      color: colors.text,
      fontSize: 26,
      fontFamily: jetBrainsMono,
    }}>{value}</div>
  </div>
);

// =========================================================================
// PHASE 5: финал-шутка про "лучший" vs "оплаченный"
// =========================================================================
const PunchlineScene: React.FC = () => {
  const frame = useCurrentFrame(); // 0..90

  const headerOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const card1Opacity = interpolate(frame, [15, 35], [0, 1], { extrapolateRight: "clamp" });
  const card2Opacity = interpolate(frame, [35, 55], [0, 1], { extrapolateRight: "clamp" });
  const equalOpacity = interpolate(frame, [55, 70], [0, 1], { extrapolateRight: "clamp" });

  // SPONSORED label на втором — мигает чаще под конец
  const sponsoredFlash = frame > 55
    ? 0.5 + 0.5 * Math.sin((frame - 55) * 0.5)
    : 0;

  return (
    <AbsoluteFill style={{
      backgroundColor: colors.bg, padding: 60,
      justifyContent: "center", alignItems: "center",
    }}>
      <div style={{
        color: colors.text, fontSize: 42, fontWeight: 800,
        fontFamily: interTight, textAlign: "center", marginBottom: 60,
        opacity: headerOpacity, lineHeight: 1.2,
      }}>
        Главный вопрос:<br/>
        чем «лучший выбор» теперь<br/>
        отличается от «оплаченного»?
      </div>

      {/* Two answer cards side-by-side */}
      <div style={{ display: "flex", gap: 24, width: "100%" }}>
        <div style={{
          flex: 1,
          padding: "28px 24px",
          backgroundColor: colors.card,
          border: `2px solid ${colors.border}`,
          borderRadius: 16,
          opacity: card1Opacity,
        }}>
          <div style={{
            display: "inline-block",
            padding: "4px 10px", marginBottom: 14,
            backgroundColor: "#27c93f", color: "#000",
            borderRadius: 6, fontSize: 14, fontWeight: 800, letterSpacing: 1.5,
          }}>BEST CHOICE</div>
          <div style={{ color: colors.text, fontSize: 24, lineHeight: 1.4 }}>
            LEGO Technic Bugatti — премиум-конструктор от 8 лет
          </div>
        </div>

        <div style={{
          flex: 1,
          padding: "28px 24px",
          backgroundColor: "rgba(255,87,34,0.08)",
          border: `2px solid ${colors.accent}`,
          borderRadius: 16,
          opacity: card2Opacity,
        }}>
          <div style={{
            display: "inline-block",
            padding: "4px 10px", marginBottom: 14,
            backgroundColor: colors.accent, color: colors.text,
            borderRadius: 6, fontSize: 14, fontWeight: 800, letterSpacing: 1.5,
            opacity: 0.5 + 0.5 * sponsoredFlash,
          }}>SPONSORED</div>
          <div style={{ color: colors.text, fontSize: 24, lineHeight: 1.4 }}>
            LEGO Technic Bugatti — премиум-конструктор от 8 лет
          </div>
        </div>
      </div>

      {/* Equals sign + caption */}
      <div style={{
        marginTop: 40, textAlign: "center",
        opacity: equalOpacity,
      }}>
        <div style={{
          color: colors.accent, fontSize: 64, fontWeight: 800,
          fontFamily: interTight, lineHeight: 1,
        }}>=</div>
        <div style={{
          color: colors.textDim, fontSize: 26,
          fontFamily: interTight, marginTop: 16,
        }}>один и тот же ответ. два разных бизнеса.</div>
      </div>
    </AbsoluteFill>
  );
};

// =========================================================================
// MAIN COMPOSITION — собирает 3 сцены через <Sequence>
// =========================================================================
export const OpenAiAdsDemo: React.FC<OpenAiAdsDemoProps> = () => {
  return (
    <AbsoluteFill style={{
      backgroundColor: colors.bg,
      fontFamily: interTight,
    }}>
      {/* Phase 1+2: ChatGPT chat with sponsored result (0-150) */}
      <Sequence from={0} durationInFrames={150}>
        <ChatScene />
      </Sequence>

      {/* Phase 3+4: Ads Manager UI (150-360) */}
      <Sequence from={150} durationInFrames={210}>
        <AdsManagerScene />
      </Sequence>

      {/* Phase 5: Punchline (360-540, 6 sec) */}
      <Sequence from={360} durationInFrames={180}>
        <PunchlineScene />
      </Sequence>

      {/* Footer brand mark — постоянный */}
      <div style={{
        position: "absolute",
        bottom: 30,
        left: 0, right: 0,
        textAlign: "center",
        color: colors.textDim, fontSize: 20,
        fontWeight: 600, letterSpacing: 4,
        fontFamily: interTight,
      }}>
        POSTULAT · AI STUDIO
      </div>
    </AbsoluteFill>
  );
};
