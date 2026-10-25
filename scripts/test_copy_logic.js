// 前端复制逻辑回归测试：在 Node 里模拟最小 DOM，验证 handleCopyClick →
// 剪贴板写入内容 + 按钮内联反馈（✓ 已复制）。仅测纯逻辑，不依赖真实浏览器。
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("console/admin.html", "utf8");
const code = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1])[0];

function makeEl() {
  return {
    value: "", textContent: "", className: "", style: {}, options: [],
    select() {}, addEventListener() {}, querySelectorAll: () => [],
    appendChild() {}, removeChild() {}, insertAdjacentHTML() {},
    setAttribute() {}, getAttribute: () => null,
  };
}

const toastCalls = [];
const host = {
  getElementById: (id) => {
    if (id === "toast") {
      const t = makeEl();
      return t;
    }
    return makeEl();
  },
  createElement: () => makeEl(),
  documentElement: { classList: { toggle() {}, add() {}, remove() {} }, style: {} },
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener() {},
  body: { appendChild() {}, removeChild() {} },
  execCommand: () => true,
};

let copied = null;
const nav = { clipboard: { writeText: (t) => { copied = t; return Promise.resolve(); } } };

const ctx = {
  document: host, navigator: nav, console,
  setTimeout: (f) => 0, clearTimeout() {},
  fetch: () => Promise.reject(new Error("no fetch in test")),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  location: { hash: "", href: "http://127.0.0.1:9998/" },
  btoa: (s) => Buffer.from(s, "binary").toString("base64"),
  FormData: function () {}, URL: { createObjectURL: () => "" },
  Math, JSON, Date, Promise, Error, Object, Array, String, Number, Boolean, Set, Map, RegExp,
};
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(code, ctx);

// 捕获 toast 调用（替换 $("toast") 的行为不便，直接包一层 toast 记录）
const cases = [
  { kind: "ref", rootname: "默认音色库", rel: "_ab_indexTTS/clips/甘雨/x.wav",
    expect: "默认音色库|_ab_indexTTS/clips/甘雨/x.wav" },
  { kind: "voice", rootname: "默认音色库", rel: "_ab_indexTTS/clips/甘雨/x.wav",
    expect: "#[音色选择][默认音色库/_ab_indexTTS/clips/甘雨/x.wav]" },
  { kind: "emo", rootname: "默认音色库", rel: "_ab_indexTTS/clips/甘雨/x.wav",
    expect: "#[情感控制][引导音频][默认音色库/_ab_indexTTS/clips/甘雨/x.wav]" },
];

let pass = 0, fail = 0;
for (const c of cases) {
  copied = null;
  const btn = { textContent: "X", disabled: false, dataset: { copy: c.kind, rootname: c.rootname, rel: c.rel } };
  ctx.handleCopyClick({ target: { closest: (sel) => (sel === "[data-copy]" ? btn : null) } });
  const ok = copied === c.expect && btn.textContent === "✓ 已复制";
  console.log(`  [${c.kind}] 剪贴板=${JSON.stringify(copied)} 按钮=${JSON.stringify(btn.textContent)} → ${ok ? "✅" : "❌"}`);
  if (ok) pass++; else fail++;
}

// 非复制按钮不应触发
copied = null;
const other = { textContent: "X", disabled: false, dataset: { copy: "unknown" } };
ctx.handleCopyClick({ target: { closest: (sel) => (sel === "[data-copy]" ? other : null) } });
const otherOk = copied === null && other.textContent === "X";
console.log(`  [unknown] 不应复制 → ${otherOk ? "✅" : "❌"}`);
if (otherOk) pass++; else fail++;

// closest 返回 null（点到别处）不应抛错
try {
  ctx.handleCopyClick({ target: { closest: () => null } });
  console.log("  [no-match] 不抛错 → ✅");
  pass++;
} catch (e) {
  console.log("  [no-match] 抛错 → ❌ " + e.message);
  fail++;
}

console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
