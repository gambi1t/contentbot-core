import "./index.css";
import "./fonts"; // top-level font loader (важно: до первого <Composition>)
import { Composition } from "remotion";
import { AiFeatureDemo } from "./scenes/AiFeatureDemo";
import { OpenAiAdsDemo } from "./scenes/OpenAiAdsDemo";
import { WebbGalaxyDemo } from "./scenes/WebbGalaxyDemo";
import { WebbOverlayDemo } from "./scenes/WebbOverlayDemo";
import { WebbGalaxyDemoV2 } from "./scenes/WebbGalaxyDemoV2";
import { WebbGalaxyDemoV3 } from "./scenes/WebbGalaxyDemoV3";
import { WebbGalaxyDemoV4 } from "./scenes/WebbGalaxyDemoV4";
import { WebbGalaxyDemoV5 } from "./scenes/WebbGalaxyDemoV5";
import { WebbGalaxyDemoV6 } from "./scenes/WebbGalaxyDemoV6";
import { OpenAiRealtimeBroll } from "./scenes/OpenAiRealtimeBroll";
import { MaksimTasksBroll } from "./scenes/MaksimTasksBroll";
import { MaksimHookChaos } from "./scenes/MaksimHookChaos";
import { MaksimHeadToSystem } from "./scenes/MaksimHeadToSystem";
import { MaksimChannelCta } from "./scenes/MaksimChannelCta";
import { MaksimFullBroll } from "./scenes/MaksimFullBroll";
import {
  InsertChaos,
  InsertPlaud,
  InsertTaskFly,
  InsertBitrix,
  InsertReport,
  InsertFreed,
} from "./scenes/MaksimInserts";
import {
  InsertWeekLoad,
  InsertCosts7,
  InsertEmptyDay,
  InsertWrongMetric,
  InsertFillBudni,
  InsertWeekFull,
} from "./scenes/MaksimInserts2";
import {
  Auto1,
  Auto2,
  Auto3,
  Auto4,
  Auto5,
  Auto6,
} from "./scenes/AutoBroll";
import {
  AiProductLaunch,
  EXAMPLE_OPENAI_REALTIME,
  EXAMPLE_CLAUDE_OPUS,
  EXAMPLE_CURSOR,
} from "./templates/AiProductLaunch";
import {
  AiToolDeepDive,
  EXAMPLE_CLAUDE_CODE,
  EXAMPLE_CURSOR_TOOL,
  EXAMPLE_LOVABLE,
} from "./templates/AiToolDeepDive";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="AiFeatureDemo"
        component={AiFeatureDemo}
        durationInFrames={240}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          product: "Claude Opus 4.7",
          feature: "extended thinking",
        }}
      />
      <Composition
        id="OpenAiAdsDemo"
        component={OpenAiAdsDemo}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          newsDate: "5 мая 2026",
        }}
      />
      <Composition
        id="WebbGalaxyDemo"
        component={WebbGalaxyDemo}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          galaxyName: "XMM-VID1-2075",
        }}
      />
      {/* OVERLAY composition — рендерится с alpha (yuva420p VP8 WebM) */}
      <Composition
        id="WebbOverlayDemo"
        component={WebbOverlayDemo}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Webb v2 — самодостаточная full-frame сцена по storyboard'у дизайн-агента */}
      <Composition
        id="WebbGalaxyDemoV2"
        component={WebbGalaxyDemoV2}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Webb v3 — реальное Webb-фото + усиленные эффекты x5 */}
      <Composition
        id="WebbGalaxyDemoV3"
        component={WebbGalaxyDemoV3}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Webb v4 — визуальный нарратив без больших надписей: контраст через анимацию */}
      <Composition
        id="WebbGalaxyDemoV4"
        component={WebbGalaxyDemoV4}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Webb v5 — V3 минус дублирующие надписи. Galaxy + animations + tech chip only */}
      <Composition
        id="WebbGalaxyDemoV5"
        component={WebbGalaxyDemoV5}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Webb v6 — путешествие через вселенную: Pillars → Carina → Cartwheel + 9 ударов */}
      <Composition
        id="WebbGalaxyDemoV6"
        component={WebbGalaxyDemoV6}
        durationInFrames={540}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* OpenAI Realtime — B-roll часть для split-layout 1080×960 (старый, hardcoded) */}
      <Composition
        id="OpenAiRealtimeBroll"
        component={OpenAiRealtimeBroll}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={{}}
      />
      {/* Maksim Life Drive — демо B-roll: ассистент ставит задачи команде */}
      <Composition
        id="MaksimTasksBroll"
        component={MaksimTasksBroll}
        durationInFrames={240}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Maksim — отдельные сцены полного ролика */}
      <Composition
        id="MaksimHookChaos"
        component={MaksimHookChaos}
        durationInFrames={270}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      <Composition
        id="MaksimHeadToSystem"
        component={MaksimHeadToSystem}
        durationInFrames={210}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      <Composition
        id="MaksimChannelCta"
        component={MaksimChannelCta}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Maksim — ПОЛНЫЙ 29-сек ролик (склейка 4 сцен) */}
      <Composition
        id="MaksimFullBroll"
        component={MaksimFullBroll}
        durationInFrames={870}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{}}
      />
      {/* Maksim — короткие B-roll-вставки (~4с) для динамичного монтажа */}
      <Composition id="InsertChaos" component={InsertChaos}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="InsertPlaud" component={InsertPlaud}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="InsertTaskFly" component={InsertTaskFly}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="InsertBitrix" component={InsertBitrix}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="InsertReport" component={InsertReport}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="InsertFreed" component={InsertFreed}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      {/* Maksim ролик #2 «Дорогие пустые часы» — B-roll про загрузку по дням */}
      <Composition id="M2WeekLoad" component={InsertWeekLoad}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="M2Costs7" component={InsertCosts7}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="M2EmptyDay" component={InsertEmptyDay}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="M2WrongMetric" component={InsertWrongMetric}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="M2FillBudni" component={InsertFillBudni}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="M2WeekFull" component={InsertWeekFull}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      {/* Maksim AUTO B-roll — Claude Code переписывает src/scenes/AutoBroll.tsx
          под каждый сценарий; эти 6 регистраций постоянные. */}
      <Composition id="AutoBroll1" component={Auto1}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="AutoBroll2" component={Auto2}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="AutoBroll3" component={Auto3}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="AutoBroll4" component={Auto4}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="AutoBroll5" component={Auto5}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      <Composition id="AutoBroll6" component={Auto6}
        durationInFrames={120} fps={30} width={1080} height={1920} defaultProps={{}} />
      {/* === TEMPLATE #1: AiProductLaunch === */}
      {/* Example 1: OpenAI GPT-Realtime-2 */}
      <Composition
        id="AiProductLaunch-OpenAi"
        component={AiProductLaunch}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={EXAMPLE_OPENAI_REALTIME}
      />
      {/* Example 2: Anthropic Claude Opus 4.7 — другая компания, другие props */}
      <Composition
        id="AiProductLaunch-Claude"
        component={AiProductLaunch}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={EXAMPLE_CLAUDE_OPUS}
      />
      {/* Example 3: Cursor 0.50 — без models (одна фича, не семейство) */}
      <Composition
        id="AiProductLaunch-Cursor"
        component={AiProductLaunch}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={EXAMPLE_CURSOR}
      />
      {/* === TEMPLATE #2: AiToolDeepDive (Бурмистров-style) === */}
      <Composition
        id="AiToolDeepDive-ClaudeCode"
        component={AiToolDeepDive}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={EXAMPLE_CLAUDE_CODE}
      />
      <Composition
        id="AiToolDeepDive-Cursor"
        component={AiToolDeepDive}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={EXAMPLE_CURSOR_TOOL}
      />
      <Composition
        id="AiToolDeepDive-Lovable"
        component={AiToolDeepDive}
        durationInFrames={360}
        fps={30}
        width={1080}
        height={960}
        defaultProps={EXAMPLE_LOVABLE}
      />
    </>
  );
};
