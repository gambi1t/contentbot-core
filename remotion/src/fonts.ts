/**
 * Top-level font loader. Импортируется один раз в Root.tsx.
 *
 * ВАЖНО (по research'у 10 мая, issue #5843):
 *   - Указываем ЯВНЫЕ subsets и weights, иначе грузятся все варианты
 *     параллельно и delayRender() падает по таймауту 58 сек.
 *   - НЕ вызывать loadFont() внутри компонентов — только top-level.
 *
 * Inter Tight = шрифт презентаций Постулата (см. project_presentation_template_racing_pitstop.md).
 * Cyrillic есть в Google Fonts с subset 'cyrillic'.
 */
import { loadFont as loadInterTight } from "@remotion/google-fonts/InterTight";
import { loadFont as loadJetBrainsMono } from "@remotion/google-fonts/JetBrainsMono";

export const { fontFamily: interTight } = loadInterTight("normal", {
  subsets: ["latin", "cyrillic"],
  weights: ["400", "600", "700", "800"],
});

export const { fontFamily: jetBrainsMono } = loadJetBrainsMono("normal", {
  subsets: ["latin", "cyrillic"],
  weights: ["400", "600"],
});

/**
 * Бренд-палитра ВПРЫСКИВАЕТСЯ per-tenant через env REMOTION_* при рендере
 * (общий проект, Option B — как HyperFrames из style_contract). Без env —
 * дефолт студии Постулат/Максим (оранж). Бот для panferov передаёт Nox Dark
 * azure (#2E9BE0) из style_contract.panferov.json.
 *   colors.accent ← REMOTION_ACCENT (panferov #2E9BE0 / дефолт #ff5722)
 * Remotion прокидывает в бандл только REMOTION_*-префиксные env.
 */
export const colors = {
  bg: process.env.REMOTION_BG || "#0a0a0a",
  card: process.env.REMOTION_CARD || "#1a1a1a",
  accent: process.env.REMOTION_ACCENT || "#ff5722",
  accentDim: process.env.REMOTION_ACCENT_DIM || "#cc3e15",
  text: process.env.REMOTION_TEXT || "#ffffff",
  textDim: process.env.REMOTION_TEXT_DIM || "#9ca3af",
  border: process.env.REMOTION_BORDER || "#2a2a2a",
};
