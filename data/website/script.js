(function () {
  var closeLabel = document.body.dataset.closeLabel || "Close";
  var copiedLabel = document.body.dataset.copiedLabel || "Copied";

  function openImage(link) {
    var overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    var image = document.createElement("img");
    image.src = link.getAttribute("href");
    image.alt = link.querySelector("img").alt;
    var close = document.createElement("button");
    close.type = "button";
    close.textContent = closeLabel;
    overlay.appendChild(image);
    overlay.appendChild(close);
    overlay.addEventListener("click", function () {
      overlay.remove();
      link.focus();
    });
    document.body.appendChild(overlay);
    close.focus();
  }

  function selectCommand(button) {
    var range = document.createRange();
    range.selectNodeContents(button.parentNode.querySelector("code"));
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  document.addEventListener("click", function (event) {
    var zoom = event.target.closest("a.zoom");
    if (zoom) {
      event.preventDefault();
      openImage(zoom);
      return;
    }
    var copy = event.target.closest(".copy");
    if (!copy) {
      return;
    }
    var label = copy.textContent;
    var done = function () {
      copy.textContent = copiedLabel;
      setTimeout(function () {
        copy.textContent = label;
      }, 1600);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(copy.dataset.copy).then(done, function () {
        selectCommand(copy);
      });
    } else {
      selectCommand(copy);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      var overlay = document.querySelector(".overlay");
      if (overlay) {
        overlay.remove();
      }
    }
  });
})();
