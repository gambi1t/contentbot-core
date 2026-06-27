/**
 * AutoBroll — B-roll-вставки, которые Claude Code переписывает под каждый
 * сценарий ролика Максима (Life Drive). НЕ редактировать вручную в проде:
 * этот файл целиком перегенерирует оркестратор `auto_broll.py`.
 *
 * Контракт (его нельзя ломать — на нём держится рендер и монтаж):
 *  - файл экспортирует ровно 6 компонентов: Auto1 … Auto6;
 *  - каждый — самостоятельная вставка 120 frames @ 30fps, 1080×1920;
 *  - компоненты зарегистрированы в Root.tsx как AutoBroll1 … AutoBroll6.
 *
 * Стиль и правила композиции — строго как в MaksimInserts2.tsx:
 *  - весь экшен в центральной полосе 1080×960 (band y∈[480,1440]);
 *  - полный визуал за ~1 сек (≈frame 30), дальше держим;
 *  - Постулат-dark: #0a0a0a + accent #ff5722 + Inter Tight.
 *
 * Ниже — БАЗОВЫЙ плейсхолдер (валидный фолбэк). Claude заменяет тела
 * Auto1…Auto6 содержательными вставками под конкретный сценарий.
 */
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { interTight, colors } from "../fonts";

type P = { [key: string]: unknown };
const ease = (t: number, p = 3) => 1 - Math.pow(1 - t, p);

const BAND_W = 1080;
const BAND_H = 960;
const BAND_TOP = (1920 - BAND_H) / 2;

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

// Базовая карточка-плейсхолдер.
const Placeholder: React.FC<{ n: number }> = ({ n }) => {
  const f = useCurrentFrame();
  const card = interpolate(f, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ fontFamily: interTight }}>
      <Ambient />
      <Band>
        <Label text="LIFE DRIVE" />
        <div
          style={{
            width: 760,
            backgroundColor: colors.card,
            border: `2px solid ${colors.border}`,
            borderRadius: 28,
            padding: 60,
            textAlign: "center",
            opacity: card,
            transform: `scale(${0.9 + 0.1 * ease(card)})`,
          }}
        >
          <div style={{ color: colors.text, fontSize: 56, fontWeight: 800 }}>
            Вставка {n}
          </div>
        </div>
      </Band>
    </AbsoluteFill>
  );
};

export const Auto1: React.FC<P> = () => <Placeholder n={1} />;
export const Auto2: React.FC<P> = () => <Placeholder n={2} />;
export const Auto3: React.FC<P> = () => <Placeholder n={3} />;
export const Auto4: React.FC<P> = () => <Placeholder n={4} />;
export const Auto5: React.FC<P> = () => <Placeholder n={5} />;
export const Auto6: React.FC<P> = () => <Placeholder n={6} />;
