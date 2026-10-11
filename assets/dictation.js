// Speech stays with the browser's recognition provider. Python receives text only.
export default function (component) {
  const { parentElement, data, setStateValue } = component;
  let runtime = parentElement.dictationRuntime;
  if (runtime && (runtime.target !== data.target || runtime.disposed)) {
    runtime.dispose();
    runtime = null;
  }
  if (!runtime) {
    const button = parentElement.querySelector("#microphone");
    const label = button.querySelector("span");
    const language = parentElement.querySelector("#language");
    const document = parentElement.ownerDocument;
    const view = document.defaultView;
    const Recognition = view.SpeechRecognition || view.webkitSpeechRecognition;
    runtime = {
      target: data.target, data, emit: setStateValue, recognition: null,
      session: `${Date.now()}-${Math.random()}`, sequence: 0,
      text: "", base: "", state: "idle", error: null, disposed: false,
      publishTimer: null, stopTimer: null,
    };
    parentElement.dictationRuntime = runtime;

    function paint() {
      const active = ["starting", "listening"].includes(runtime.state);
      button.setAttribute("aria-pressed", String(active));
      button.setAttribute("aria-label", active ? "Zatrzymaj dyktowanie" : "Dyktuj pytanie");
      label.textContent = active ? "Stop" : "Mów";
      button.disabled = !Recognition || runtime.data.disabled;
      language.disabled = active || runtime.data.disabled;
      button.title = Recognition ? "Dyktuj do pola wiadomości. Głos przetwarza dostawca przeglądarki." : "Otwórz aplikację w Chrome lub Edge, aby dyktować.";
    }

    function publish() {
      clearTimeout(runtime.publishTimer);
      runtime.publishTimer = null;
      if (!runtime.disposed) {
        runtime.emit("update", { target: runtime.target, session: runtime.session,
          sequence: ++runtime.sequence, text: runtime.text, state: runtime.state, error: runtime.error });
      }
      paint();
    }

    function stop() {
      clearTimeout(runtime.stopTimer);
      if (runtime.recognition) runtime.recognition.stop();
    }

    button.onclick = () => {
      if (runtime.data.disabled || !Recognition) return;
      if (["starting", "listening"].includes(runtime.state)) { stop(); return; }
      // Reading the current native field preserves text typed before dictation.
      runtime.base = document.querySelector(".st-key-sql_input textarea")?.value.trim() || "";
      if (runtime.base.length > runtime.data.max_chars) {
        runtime.error = "limit";
        runtime.state = "error";
        runtime.text = "";
        publish();
        return;
      }
      runtime.text = runtime.base;
      runtime.error = null;
      runtime.state = "starting";
      const recognition = new Recognition();
      runtime.recognition = recognition;
      recognition.lang = language.value;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;
      recognition.onstart = () => {
        if (runtime.disposed || runtime.recognition !== recognition) return;
        runtime.state = "listening";
        runtime.stopTimer = setTimeout(() => { runtime.error = "timeout"; stop(); }, runtime.data.max_seconds * 1000);
        publish();
      };
      recognition.onresult = event => {
        if (runtime.disposed || runtime.recognition !== recognition) return;
        // Rebuild the transcript: interim corrections must replace earlier words.
        const words = Array.from(event.results, result => result[0].transcript.trim()).filter(Boolean).join(" ");
        const text = [runtime.base, words].filter(Boolean).join(" ");
        runtime.text = text.slice(0, runtime.data.max_chars);
        if (text.length > runtime.data.max_chars) { runtime.error = "limit"; stop(); }
        if (!runtime.publishTimer) runtime.publishTimer = setTimeout(publish, 180);
      };
      recognition.onerror = event => {
        if (runtime.disposed || runtime.recognition !== recognition) return;
        runtime.error = event.error === "aborted" ? null : event.error;
        runtime.state = runtime.error ? "error" : "idle";
        publish();
      };
      recognition.onend = () => {
        if (runtime.disposed || runtime.recognition !== recognition) return;
        clearTimeout(runtime.stopTimer);
        runtime.recognition = null;
        runtime.state = runtime.error ? "error" : "idle";
        publish();
      };
      publish();
      try { recognition.start(); }
      catch { runtime.state = "error"; runtime.error = "unknown"; runtime.recognition = null; publish(); }
    };

    const visibility = () => { if (document.hidden) stop(); };
    const pagehide = () => runtime.dispose();
    document.addEventListener("visibilitychange", visibility);
    view.addEventListener("pagehide", pagehide);
    runtime.dispose = () => {
      runtime.disposed = true;
      clearTimeout(runtime.publishTimer);
      clearTimeout(runtime.stopTimer);
      if (runtime.recognition) runtime.recognition.abort();
      document.removeEventListener("visibilitychange", visibility);
      view.removeEventListener("pagehide", pagehide);
    };
    runtime.paint = paint;
    if (!Recognition) { runtime.state = "error"; runtime.error = "unsupported"; publish(); }
  }
  runtime.data = data;
  runtime.emit = setStateValue;
  if (data.disabled && runtime.recognition) runtime.recognition.abort();
  runtime.paint();
  // Streamlit may rerender while speech is arriving. Stop only on real unmount.
  return () => queueMicrotask(() => { if (!parentElement.isConnected) runtime.dispose(); });
}
