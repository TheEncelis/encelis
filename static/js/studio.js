/* encelis studio — drag-and-drop ordering.

   Markup contract:
     <div data-sortable data-kind="topic" data-parent="12" data-group="topics" data-url="/studio/curriculum/reorder/">
       <div data-id="5"> … <button class="drag-handle">…</button> … </div>
     </div>
   The full order of each touched list is POSTed; the server validates it.
   Keyboard: focus a handle and press ArrowUp / ArrowDown. */
(function () {
  "use strict";

  function csrf() {
    const meta = document.querySelector("meta[name=csrf-token]");
    return meta ? meta.content : "";
  }

  let toastTimer = null;
  function toast(text, ok) {
    let el = document.getElementById("studio-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "studio-toast";
      el.className = "studio-toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.dataset.state = ok ? "ok" : "error";
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), ok ? 1400 : 4000);
  }

  function items(list) {
    return Array.from(list.children).filter((child) => child.dataset.id);
  }

  function refreshEmpty(list) {
    list.classList.toggle("is-empty", items(list).length === 0);
  }

  async function save(list) {
    const body = new URLSearchParams();
    body.append("kind", list.dataset.kind);
    body.append("parent", list.dataset.parent);
    items(list).forEach((item) => body.append("ids", item.dataset.id));
    refreshEmpty(list);
    try {
      const response = await fetch(list.dataset.url, {
        method: "POST",
        headers: { "X-CSRFToken": csrf(), "Content-Type": "application/x-www-form-urlencoded" },
        body,
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(response.status);
      toast("Tartib saqlandi", true);
      return true;
    } catch (error) {
      toast("Tartib saqlanmadi — sahifani yangilang", false);
      return false;
    }
  }

  function init(root) {
    if (!window.Sortable) return;
    root.querySelectorAll("[data-sortable]").forEach((list) => {
      refreshEmpty(list);
      if (list._sortable) return;
      list._sortable = Sortable.create(list, {
        group: list.dataset.group || list.dataset.kind,
        handle: ".drag-handle",
        draggable: "[data-id]",
        animation: 160,
        ghostClass: "is-ghost",
        chosenClass: "is-chosen",
        dragClass: "is-dragging",
        forceFallback: true, // pointer events: identical for mouse, touch and pen
        fallbackTolerance: 3,
        fallbackOnBody: true,
        fallbackClass: "is-dragging",
        onEnd(event) {
          if (event.from !== event.to) {
            save(event.to).then((ok) => ok && save(event.from));
          } else if (event.oldIndex !== event.newIndex) {
            save(event.to);
          }
        },
      });
    });
  }

  /* Keyboard reordering for handles. */
  document.addEventListener("keydown", (event) => {
    const handle = event.target.closest && event.target.closest(".drag-handle");
    if (!handle || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    const item = handle.closest("[data-id]");
    const list = item && item.parentElement;
    if (!list || !list.hasAttribute("data-sortable")) return;
    event.preventDefault();
    const siblings = items(list);
    const index = siblings.indexOf(item);
    const target = event.key === "ArrowUp" ? siblings[index - 1] : siblings[index + 1];
    if (!target) return;
    if (event.key === "ArrowUp") list.insertBefore(item, target);
    else list.insertBefore(item, target.nextSibling);
    handle.focus();
    save(list);
  });

  document.addEventListener("DOMContentLoaded", () => init(document));
  document.addEventListener("htmx:afterSettle", (event) => init(event.detail.elt.ownerDocument || document));
})();
