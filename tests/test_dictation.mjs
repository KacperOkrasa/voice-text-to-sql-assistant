// Test browser event handling with a fake recognizer; never capture real audio.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../assets/dictation.js", import.meta.url), "utf8");
const { default: mount } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function fixture(supported = true) {
  const updates = [], recognizers = [];
  const label = { textContent: "Mów" };
  const button = { querySelector: () => label, setAttribute() {} };
  const language = { value: "pl-PL" };
  const input = { value: "" };
  class Recognition {
    constructor() { recognizers.push(this); }
    start() { this.onstart(); }
    stop() { this.stopped = true; this.onend(); }
    abort() { this.aborted = true; this.onend(); }
    result(...texts) { this.onresult({ results: texts.map(text => [{ transcript: text }]) }); }
  }
  const document = {
    querySelector: () => input, addEventListener() {}, removeEventListener() {},
    defaultView: { SpeechRecognition: supported ? Recognition : undefined, addEventListener() {}, removeEventListener() {} },
  };
  const root = { ownerDocument: document, isConnected: true,
    querySelector: selector => selector === "#microphone" ? button : language };
  const component = { parentElement: root, data: { target: "chat-1", disabled: false, max_chars: 2000, max_seconds: 120 },
    setStateValue: (key, value) => updates.push(value) };
  const cleanup = mount(component);
  return { component, root, input, button, updates, recognizers, cleanup, language };
}

test("interim corrections replace words, preserve typed prefix, and survive rerenders", t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture();
  f.input.value = "Pokaż";
  f.button.onclick();
  const recognition = f.recognizers[0];
  assert.equal(recognition.lang, "pl-PL");
  assert.equal(recognition.interimResults, true);
  recognition.result("przyhód");
  t.mock.timers.tick(180);
  assert.equal(f.updates.at(-1).text, "Pokaż przyhód");
  mount(f.component);
  recognition.result("przychód", "w sierpniu");
  t.mock.timers.tick(180);
  assert.equal(f.updates.at(-1).text, "Pokaż przychód w sierpniu");
  assert.equal(f.recognizers.length, 1);
  f.button.onclick();
  assert.equal(recognition.stopped, true);
  assert.equal(f.updates.at(-1).state, "idle");
  assert.equal(f.updates.at(-1).text, "Pokaż przychód w sierpniu");
});

test("changing conversation or unmounting stops the microphone", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture();
  f.button.onclick();
  const cleanup = mount({ ...f.component, data: { ...f.component.data, target: "chat-2" } });
  assert.equal(f.recognizers[0].aborted, true);
  f.button.onclick();
  f.root.isConnected = false;
  cleanup();
  await new Promise(resolve => queueMicrotask(resolve));
  assert.equal(f.recognizers[1].aborted, true);
});

test("unsupported browser never starts recognition", () => {
  const f = fixture(false);
  assert.equal(f.button.disabled, true);
  assert.equal(f.updates.at(-1).error, "unsupported");
  assert.equal(f.recognizers.length, 0);
});

test("speech limit and timeout stop listening with a reviewable draft", t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture();
  f.button.onclick();
  f.recognizers[0].result("x".repeat(2001));
  assert.equal(f.updates.at(-1).error, "limit");
  assert.equal(f.updates.at(-1).text.length, 2000);
  f.button.onclick();
  t.mock.timers.tick(120000);
  assert.equal(f.updates.at(-1).error, "timeout");
  assert.equal(f.recognizers[1].stopped, true);
});
