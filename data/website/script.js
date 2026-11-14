"use strict";

function rememberChosenLanguage() {
  document.querySelectorAll("[data-language]").forEach((link) => {
    link.addEventListener("click", () => {
      try {
        localStorage.setItem("custicon-language", link.dataset.language);
      } catch {
        return;
      }
    });
  });
}

function enlargeScreenshotsOnClick() {
  const lightbox = document.createElement("dialog");
  lightbox.className = "lightbox";
  lightbox.setAttribute("aria-label", document.body.dataset.closeLabel);
  const picture = document.createElement("img");
  lightbox.append(picture);
  document.body.append(lightbox);
  lightbox.addEventListener("click", () => lightbox.close());
  document.querySelectorAll("a.zoom").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      picture.src = link.href;
      picture.alt = link.querySelector("img").alt;
      lightbox.showModal();
    });
  });
}

function copyCommands() {
  document.querySelectorAll(".copy").forEach((button) => {
    button.addEventListener("click", () => {
      const label = button.textContent;
      const done = () => {
        button.textContent = document.body.dataset.copiedLabel;
        button.classList.add("done");
        setTimeout(() => {
          button.textContent = label;
          button.classList.remove("done");
        }, 1600);
      };
      const select = () => {
        const range = document.createRange();
        range.selectNodeContents(button.parentNode.querySelector("code"));
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(button.dataset.copy).then(done, select);
      } else {
        select();
      }
    });
  });
}

function runCarousels() {
  const calmRequested = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const savingData = Boolean(navigator.connection && navigator.connection.saveData);
  document.querySelectorAll(".carousel").forEach((carousel) => {
    const labels = carousel.dataset;
    const stage = carousel.querySelector(".stage");
    const slides = [...carousel.querySelectorAll(".slide")];
    const count = slides.length;
    let current = 0;
    let manualPause = calmRequested || savingData;
    let visible = true;
    let hovered = false;

    function makeButton(className, text, label) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = text;
      if (label) {
        button.setAttribute("aria-label", label);
      }
      return button;
    }

    const previous = makeButton("arrow prev", "‹", labels.labelPrevious);
    const next = makeButton("arrow next", "›", labels.labelNext);
    stage.append(previous, next);
    const controls = document.createElement("div");
    controls.className = "controls";
    const toggle = makeButton("toggle", "", "");
    const rail = document.createElement("div");
    rail.className = "rail";
    rail.setAttribute("aria-hidden", "true");
    controls.append(toggle, rail);
    const thumbnails = document.createElement("div");
    thumbnails.className = "thumbs";
    const state = document.createElement("p");
    state.className = "state";
    state.setAttribute("aria-live", "polite");
    carousel.append(controls, thumbnails, state);

    const dots = slides.map((slide, index) => {
      const dot = makeButton("dot", "", labels.labelImage.replace("{n}", index + 1).replace("{total}", count));
      dot.tabIndex = -1;
      dot.append(Object.assign(document.createElement("span"), { className: "fill" }));
      dot.addEventListener("click", () => show(index));
      rail.append(dot);
      slide.setAttribute("role", "group");
      slide.setAttribute("aria-roledescription", labels.labelSlide);
      slide.setAttribute("aria-label", `${index + 1} / ${count}`);
      return dot;
    });

    const thumbnailButtons = slides.map((slide, index) => {
      const button = makeButton("thumb", "", slide.querySelector("figcaption").textContent);
      const picture = document.createElement("img");
      picture.src = slide.dataset.thumb;
      picture.alt = "";
      picture.width = 320;
      picture.height = 230;
      picture.loading = "lazy";
      button.append(picture);
      button.addEventListener("click", () => show(index));
      thumbnails.append(button);
      return button;
    });

    stage.setAttribute("role", "group");
    stage.setAttribute("aria-roledescription", labels.labelCarousel);
    stage.setAttribute("aria-label", labels.labelScreenshots);
    stage.tabIndex = 0;
    carousel.classList.add("enhanced", "settling");

    function refresh() {
      const held = hovered || manualPause || !visible || document.hidden;
      carousel.dataset.held = String(held);
      carousel.dataset.playing = String(!manualPause);
      toggle.textContent = manualPause ? "▶" : "❚❚";
      toggle.setAttribute("aria-pressed", String(manualPause));
      toggle.setAttribute("aria-label", manualPause ? labels.labelPlay : labels.labelPause);
      if ((calmRequested || savingData) && manualPause) {
        state.textContent = labels.labelCalm;
      } else {
        state.textContent = hovered && !manualPause ? labels.labelHeld : "";
      }
    }

    function show(index) {
      current = (index + count) % count;
      slides.forEach((slide, position) => {
        slide.classList.toggle("on", position === current);
        slide.classList.toggle("past", position < current);
      });
      dots.forEach((dot, position) => {
        dot.classList.remove("now");
        dot.classList.toggle("done", position < current);
        dot.setAttribute("aria-current", String(position === current));
      });
      void carousel.offsetWidth;
      dots[current].classList.add("now");
      thumbnailButtons.forEach((button, position) => {
        button.setAttribute("aria-current", String(position === current));
      });
      const active = thumbnailButtons[current];
      if (thumbnails.scrollWidth > thumbnails.clientWidth) {
        thumbnails.scrollTo({
          left: active.offsetLeft - (thumbnails.clientWidth - active.clientWidth) / 2,
          behavior: calmRequested ? "auto" : "smooth",
        });
      }
      refresh();
    }

    function hold(value) {
      hovered = value;
      refresh();
    }

    carousel.addEventListener("animationend", (event) => {
      if (event.animationName === "carousel-fill") {
        show(current + 1);
      }
    });
    previous.addEventListener("click", () => show(current - 1));
    next.addEventListener("click", () => show(current + 1));
    toggle.addEventListener("click", () => {
      manualPause = !manualPause;
      refresh();
    });
    stage.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight") {
        event.preventDefault();
        show(current + 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        show(current - 1);
      }
    });
    carousel.addEventListener("mouseenter", () => hold(true));
    carousel.addEventListener("mouseleave", () => hold(false));
    carousel.addEventListener("focusin", () => hold(true));
    carousel.addEventListener("focusout", () => hold(false));

    let startX = null;
    let swiped = false;
    stage.addEventListener("pointerdown", (event) => {
      startX = event.clientX;
      swiped = false;
    });
    stage.addEventListener("pointerup", (event) => {
      if (startX !== null && Math.abs(event.clientX - startX) > 40) {
        swiped = true;
        show(current + (event.clientX < startX ? 1 : -1));
      }
      startX = null;
    });
    stage.addEventListener("click", (event) => {
      if (swiped) {
        event.preventDefault();
        event.stopImmediatePropagation();
        swiped = false;
      }
    }, true);

    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        visible = entries[0].isIntersecting;
        refresh();
      }, { threshold: 0.4 }).observe(carousel);
    }
    document.addEventListener("visibilitychange", refresh);
    show(0);
    requestAnimationFrame(() => carousel.classList.remove("settling"));
  });
}

rememberChosenLanguage();
enlargeScreenshotsOnClick();
copyCommands();
runCarousels();
