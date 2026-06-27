/**
 * MaksimFullBroll — полный 29-сек B-roll ролик Максима (Life Drive).
 *
 * Склейка 4 motion-сцен встык под закадровый сценарий «уволил себя из роли
 * надзирателя → ассистент ставит задачи → отчёт → голова свободна → CTA».
 *
 * 29 сек, 870 frames @ 30fps, 1080×1920.
 *   Сцена 1  HookChaos     270 (0-270)   — хук: хаос «надзирателя»
 *   Сцена 2  TasksBroll    240 (270-510) — ассистент ставит задачи + отчёт
 *   Сцена 3  HeadToSystem  210 (510-720) — метафора «голова → система»
 *   Сцена 4  ChannelCta    150 (720-870) — CTA Telegram-канал
 *
 * Каждая сцена внутри <Series.Sequence> получает локальный кадр от 0 —
 * сцены написаны под это.
 */
import { AbsoluteFill, Series } from "remotion";
import { MaksimHookChaos } from "./MaksimHookChaos";
import { MaksimTasksBroll } from "./MaksimTasksBroll";
import { MaksimHeadToSystem } from "./MaksimHeadToSystem";
import { MaksimChannelCta } from "./MaksimChannelCta";

export type MaksimFullBrollProps = {
  [key: string]: unknown;
};

export const MaksimFullBroll: React.FC<MaksimFullBrollProps> = () => {
  return (
    <AbsoluteFill>
      <Series>
        <Series.Sequence durationInFrames={270}>
          <MaksimHookChaos />
        </Series.Sequence>
        <Series.Sequence durationInFrames={240}>
          <MaksimTasksBroll />
        </Series.Sequence>
        <Series.Sequence durationInFrames={210}>
          <MaksimHeadToSystem />
        </Series.Sequence>
        <Series.Sequence durationInFrames={150}>
          <MaksimChannelCta />
        </Series.Sequence>
      </Series>
    </AbsoluteFill>
  );
};
