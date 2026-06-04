/**
 * test_hf_inspect_layout.mjs — TDD для детектора hf_inspect_layout.mjs.
 *
 * Гоняет детектор на 5 фикстурах с известными дефектами и проверяет, что
 * найдены ОЖИДАЕМЫЕ типы issue (не больше, не меньше).
 *
 * Фикстуры (test_fixtures/):
 *   fixture_clean            → ok:true (нет issues)
 *   fixture_overlap          → overlap (два текста пересекаются)
 *   fixture_offscreen_card   → offscreen edge=right (карточка-фон за краем)
 *   fixture_mingap           → crowding (зазор 8px < MIN_GAP)
 *   fixture_title_multiline  → ok:true (заголовок div+span, ancestry-skip)
 *
 * Run (на сервере, с HYPERFRAMES_BROWSER_PATH):
 *   node test_hf_inspect_layout.mjs
 * Exit 0 = все PASS, 1 = есть FAIL.
 */
import { execFileSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DETECTOR = path.join(__dirname, "hf_inspect_layout.mjs");
const FIX = path.join(__dirname, "test_fixtures");

function run(fixture) {
  try {
    const out = execFileSync("node", [DETECTOR, path.join(FIX, fixture)], {
      encoding: "utf8",
      env: process.env,
    });
    return JSON.parse(out);
  } catch (e) {
    // детектор выходит с кодом 1 когда есть issues — stdout всё равно валиден
    if (e.stdout) {
      try { return JSON.parse(e.stdout); } catch {}
    }
    return { _error: e.message, _stdout: e.stdout, _stderr: e.stderr };
  }
}

const errors = [];
function assert(cond, msg) {
  console.log(`  ${cond ? "OK" : "FAIL"} ${msg}`);
  if (!cond) errors.push(msg);
}
function hasIssue(res, pred) {
  return Array.isArray(res.issues) && res.issues.some(pred);
}

console.log("=".repeat(60));
console.log("test_hf_inspect_layout");
console.log("=".repeat(60));

// 1. clean → ok
console.log("\n-- fixture_clean: чисто --");
{
  const r = run("fixture_clean.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(r.ok === true, `ok:true (got ok=${r.ok}, count=${r.count})`);
}

// 2. overlap → есть overlap issue
console.log("\n-- fixture_overlap: пересечение текстов --");
{
  const r = run("fixture_overlap.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(hasIssue(r, (i) => i.type === "overlap"), "найден issue type=overlap");
}

// 3. offscreen_card → offscreen right (КАРТОЧКА, не текст)
console.log("\n-- fixture_offscreen_card: карточка за правым краем --");
{
  const r = run("fixture_offscreen_card.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(
    hasIssue(r, (i) => i.type === "offscreen" && /right/.test(i.edge || "")),
    "найден offscreen edge=right (фон карточки)"
  );
}

// 4. mingap → crowding
console.log("\n-- fixture_mingap: зазор 8px (тесно) --");
{
  const r = run("fixture_mingap.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(hasIssue(r, (i) => i.type === "crowding"), "найден issue type=crowding");
}

// 5. title_multiline → ok (ancestry-skip, НЕТ ложного overlap)
console.log("\n-- fixture_title_multiline: заголовок div+span (не ложный позитив) --");
{
  const r = run("fixture_title_multiline.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(
    !hasIssue(r, (i) => i.type === "overlap"),
    `НЕТ ложного overlap внутри заголовка (issues: ${JSON.stringify(r.issues || [])})`
  );
}

// 6. H1: chart rows — плотные метки внутри карточки = НЕ crowding
console.log("\n-- fixture_chart_rows: плотные метки чарта (H1: не должно быть ложного crowding) --");
{
  const r = run("fixture_chart_rows.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(
    !hasIssue(r, (i) => i.type === "crowding"),
    `НЕТ ложного crowding для меток внутри одной карточки (issues: ${JSON.stringify(r.issues || [])})`
  );
}

// 7. C1: tiny overlap — узкое пересечение должно ловиться (dead-zone fix)
console.log("\n-- fixture_tiny_overlap: ix=5 iy=3 (C1: dead-zone — должно ловиться) --");
{
  const r = run("fixture_tiny_overlap.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(
    hasIssue(r, (i) => i.type === "overlap" || i.type === "crowding"),
    `маленькое пересечение должно ловиться (issues: ${JSON.stringify(r.issues || [])})`
  );
}

// 8. H2: одинаковый текст в РАЗНЫХ местах — 2 issue, не 1
console.log("\n-- fixture_repeated_labels: 2 пары crowding с одинаковым текстом (H2) --");
{
  const r = run("fixture_repeated_labels.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  const cr = (r.issues || []).filter((i) => i.type === "crowding");
  assert(
    cr.length >= 2,
    `ожидаем ≥2 crowding-issue (одинаковый текст, разные координаты), got ${cr.length}: ${JSON.stringify(cr)}`
  );
}

// 9. G1: ghost-text (декор, opacity<0.2) НЕ даёт ложный overlap
console.log("\n-- fixture_ghost_text: ghost-декор (opacity 0.06) не считается overlap --");
{
  const r = run("fixture_ghost_text.html");
  assert(!r._error, `детектор отработал (${r._error || "ok"})`);
  assert(
    !hasIssue(r, (i) => i.type === "overlap"),
    `НЕТ ложного overlap с ghost-текстом (issues: ${JSON.stringify(r.issues || [])})`
  );
}

console.log("");
if (errors.length) {
  console.log(`FAIL: ${errors.length} assertion(s)`);
  process.exit(1);
}
console.log("PASS");
process.exit(0);
