/**
 * Design tokens — единые константы для всех сцен и компонентов.
 *
 * Источник правил:
 *   - 14 правил из remotion_design_guide_2026-05-10.md
 *   - 18 типов из remotion_capabilities_inventory_2026-05-10.md
 *   - Палитра panferov Nox Dark (экспорт POSTULAT — имя историческое)
 *   - Cosmic palette для научных сцен (WebbGalaxyDemo)
 *
 * НИКОГДА не использовать magic numbers в сценах — всё через эти токены.
 * Любая правка цвета/размера/тайминга — здесь, не в .tsx.
 */

import { Easing } from "remotion";

// ============================================================================
// LAYOUT — 1080×1920 9:16
// ============================================================================
export const SAFE_AREA = 100; // правило 9: padding со всех сторон, не 60
export const VIEWPORT = { width: 1080, height: 1920 };
export const FPS = 30;

// ============================================================================
// TYPOGRAPHY — иерархия размеров (правило 2)
// ============================================================================
export const FONT_SIZE = {
  hero: 110, // главный текст в кадре
  heroXL: 140, // ключевое слово, scale-up
  sub: 60, // подзаголовок
  body: 36, // основной текст
  caption: 26, // метаданные, мелкий текст
  legal: 18, // disclaimer, source attribution — ТОЛЬКО для legal
} as const;

export const FONT_WEIGHT = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
  black: 800,
} as const;

export const LETTER_SPACING = {
  // На крупных шрифтах кириллица Inter Tight нуждается в +0.5..+1
  hero: 0.5,
  body: 0,
  // Капс-метки и lower-third — широкое spacing
  caps: 4,
  capsWide: 6,
} as const;

export const LINE_HEIGHT = {
  hero: 1.05, // tight для крупного текста
  body: 1.4,
  comfortable: 1.5,
} as const;

// ============================================================================
// COLOR — Постулат (tech) + Cosmic (science)
// ============================================================================
// POSTULAT — имя историческое. Палитра ВПРЫСКИВАЕТСЯ per-tenant через env
// REMOTION_* при рендере (Option B, как fonts.ts). Без env — дефолт Постулат/
// Максим (оранж); бот для panferov передаёт Nox Dark azure из style_contract.
export const POSTULAT = {
  bg: process.env.REMOTION_BG || "#0a0a0a",
  card: process.env.REMOTION_CARD || "#1a1a1a",
  border: process.env.REMOTION_BORDER || "#2a2a2a",
  accent: process.env.REMOTION_ACCENT || "#ff5722",
  accentDim: process.env.REMOTION_ACCENT_DIM || "#cc3e15",
  text: process.env.REMOTION_TEXT || "#ffffff",
  textDim: process.env.REMOTION_TEXT_DIM || "#9ca3af",
};

export const COSMIC = {
  bgDeep: "#05071a",
  bgMid: "#0d1240",
  bgLight: "#1a1f5c",
  starWhite: "#ffffff",
  starGold: "#ffd700",
  starWarm: "#ffe5b8",
  galaxyPurple: "#9d4edd",
  galaxyBlue: "#5e60ce",
  text: "#ffffff",
  textSecondary: "#c8d3ff",
  textDim: "#7a85b8",
} as const;

// Универсальная палитра для caption-стилей и hot-word подсветки
export const ACCENTS = {
  warm: "#ffd700", // gold
  hot: process.env.REMOTION_ACCENT || "#ff5722", // бренд-акцент (env per-tenant)
  cool: "#5e60ce", // blue
  success: "#27c93f",
  danger: "#ef476f",
} as const;

// ============================================================================
// MOTION — spring и easing (правила 5, 12)
// ============================================================================

// Spring config — ВЕЗДЕ где появляется важный элемент
export const SPRING = {
  // Стандартный bounce — universally good для reveal'ов
  default: { damping: 12, stiffness: 200, mass: 0.8 },
  // Тяжёлый, медленный — для hero-headline
  heavy: { damping: 18, stiffness: 120, mass: 1.2 },
  // Быстрый, без bounce — для UI элементов (кнопки, badges)
  snappy: { damping: 20, stiffness: 300, mass: 0.5 },
  // Микро-bounce для мелких элементов
  subtle: { damping: 15, stiffness: 180, mass: 0.6 },
} as const;

// Easing для interpolate — каждый interpolate должен иметь явный easing
export const EASING = {
  outCubic: Easing.out(Easing.cubic),
  inOutCubic: Easing.inOut(Easing.cubic),
  // Cinematic smooth-out — для длинных движений
  cinematic: Easing.bezier(0.16, 1, 0.3, 1),
  // Fast-out — для быстрых появлений
  fastOut: Easing.bezier(0.4, 0, 0.2, 1),
} as const;

// ============================================================================
// TIMING — типовые длительности в frames @ 30fps (правила 1, 6, 7)
// ============================================================================
export const FRAMES = {
  // Правило 1 — open with breath
  breath: 15, // 0.5 сек паузы в начале сцены
  // Правило 6 — punch every 2.5 sec
  punchInterval: 75, // ≈ 2.5 сек
  // Правило 7 — text hold
  textHoldMin: 36, // ≈ 1.2 сек минимум
  textHoldMax: 75, // ≈ 2.5 сек максимум
  // Стандартные reveal'ы
  fadeIn: 12, // 0.4 сек
  slideIn: 18, // 0.6 сек
  springSettle: 24, // 0.8 сек
} as const;

// ============================================================================
// OVERLAY — параметры для alpha-WebM рендера (Тип 16)
// ============================================================================
export const OVERLAY = {
  // Стандартное расположение floating-элементов
  cornerOffset: 60, // отступ от края для лого/badge
  // Стандартные длительности overlay'ев
  shortBadge: 60, // 2 сек — мелкий badge типа NEW/SPONSORED
  caption: 45, // 1.5 сек — caption-word над лицом
  logoFade: 90, // 3 сек — лого инструмента в углу
  lowerThird: 120, // 4 сек — lower-third с цитатой
} as const;

// ============================================================================
// HELPER — выбор бренд-палитры по контексту
// ============================================================================
export type BrandContext = "postulat" | "cosmic";

export const getBrandColors = (brand: BrandContext) =>
  brand === "cosmic" ? COSMIC : POSTULAT;
