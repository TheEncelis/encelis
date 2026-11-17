/* encelis — small, explicit Alpine components. No business logic lives here:
   the server decides correctness, progression and scheduling. */
(function () {
  "use strict";

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }

  document.addEventListener("alpine:init", () => {
    /* Measures how long the learner spends on a question (sent as elapsed_ms;
       the server caps it with its own clock). */
    Alpine.data("stopwatch", () => ({
      startedAt: performance.now(),
      elapsed() { return Math.round(performance.now() - this.startedAt); },
    }));

    /* Countdown bar for timed challenges. Submits the form when time runs out. */
    Alpine.data("countdown", (seconds) => ({
      left: seconds,
      total: seconds,
      timer: null,
      init() {
        const started = performance.now();
        this.timer = setInterval(() => {
          this.left = Math.max(0, this.total - (performance.now() - started) / 1000);
          if (this.left <= 0) {
            clearInterval(this.timer);
            const form = this.$root.querySelector("form");
            if (form && !form.dataset.sent) { form.dataset.sent = "1"; htmx.trigger(form, "submit"); }
          }
        }, 100);
      },
      destroy() { clearInterval(this.timer); },
      get ratio() { return this.left / this.total; },
    }));

    /* Sentence construction: tap tokens to build the sentence, tap again to remove. */
    Alpine.data("builder", (tokens) => ({
      tokens: tokens.map((text, id) => ({ id, text })),
      picked: [],
      pick(token) { if (!this.picked.includes(token.id)) this.picked.push(token.id); },
      drop(id) { this.picked = this.picked.filter((p) => p !== id); },
      undo() { this.picked.pop(); },
      used(token) { return this.picked.includes(token.id); },
      get sentence() { return this.picked.map((id) => this.tokens[id].text).join(" "); },
      get complete() { return this.picked.length === this.tokens.length; },
    }));

    /* Keyboard shortcuts for multiple choice (1–5). */
    Alpine.data("choices", () => ({
      selected: null,
      key(event) {
        if (event.target.matches("input[type=text], textarea")) return;
        const n = parseInt(event.key, 10);
        const inputs = this.$root.querySelectorAll("input[name=option]");
        if (n >= 1 && n <= inputs.length) { inputs[n - 1].checked = true; this.selected = n - 1; }
      },
    }));

    Alpine.data("themeSwitch", (initial) => ({
      theme: initial,
      set(value) { this.theme = value; applyTheme(value); },
    }));
  });

  /* Send CSRF token with every htmx request. */
  document.addEventListener("htmx:configRequest", (event) => {
    const token = document.querySelector("meta[name=csrf-token]");
    if (token) event.detail.headers["X-CSRFToken"] = token.content;
  });

  /* Move focus to the new exercise after a swap, for keyboard and screen-reader users. */
  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    const focusable = target.querySelector("[autofocus], [data-focus]");
    if (focusable) focusable.focus({ preventScroll: true });
    if (target.id === "stage") window.scrollTo({ top: 0, behavior: "smooth" });
  });

  /* Submit the current exercise with Enter on text inputs; Enter on feedback continues. */
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    const next = document.querySelector("[data-next]");
    if (next && !event.target.matches("textarea, input, select, button")) { event.preventDefault(); next.click(); }
  });
})();
