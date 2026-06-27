/**
 * WebbOverlayDemo — демонстрация alpha-WebM overlay для космо-новости.
 *
 * 5 сек / 150 frames @ 30fps / 1080×1920 9:16
 *
 * НИЧЕГО на полный экран — только floating-элементы:
 *   0.0–2.0с: телескоп-emoji в правом-верхнем углу с подписью «JAMES WEBB»
 *   1.5–3.5с: caption-word «НЕ ВРАЩАЕТСЯ» в lower-third (поверх лица)
 *   3.5–5.0с: emoji-знак вопроса в верхнем-левом + caption «ПОЧЕМУ?»
 *
 * Рендерится с alpha-каналом (transparent background) → ffmpeg-overlay
 * накладывает поверх talking-head HeyGen-аватара.
 *
 * Команда рендера:
 *   npx remotion render WebbOverlayDemo out/webb-overlay.webm \
 *     --pixel-format=yuva420p --codec=vp8 --no-audio
 */
import { AbsoluteFill, Sequence } from "remotion";
import { CornerLogo } from "../components/Overlay/CornerLogo";
import { CaptionWord } from "../components/Overlay/CaptionWord";

export type WebbOverlayDemoProps = {
  [key: string]: unknown;
};

export const WebbOverlayDemo: React.FC<WebbOverlayDemoProps> = () => {
  return (
    // backgroundColor: 'transparent' — критично для alpha-канала
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {/* Phase 1 (0-60): телескоп-icon в углу с подписью JAMES WEBB */}
      <Sequence from={0} durationInFrames={60}>
        <CornerLogo
          emoji="🔭"
          label="JAMES WEBB"
          corner="top-right"
          color="warm"
        />
      </Sequence>

      {/* Phase 2 (45-105): hot caption-word в lower-third */}
      <Sequence from={45} durationInFrames={60}>
        <CaptionWord
          text="НЕ ВРАЩАЕТСЯ"
          color="warm"
          position="lower-third"
        />
      </Sequence>

      {/* Phase 3 (105-150): question emoji + ПОЧЕМУ caption */}
      <Sequence from={105} durationInFrames={45}>
        <CornerLogo
          emoji="🤔"
          label="ПОЧЕМУ?"
          corner="top-left"
          color="hot"
        />
      </Sequence>
    </AbsoluteFill>
  );
};
