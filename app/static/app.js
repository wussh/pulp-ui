window.pulpOps = window.pulpOps || {};

// Capture the server-issued CSRF token for same-origin mutations.
document.addEventListener("DOMContentLoaded", function () {
  fetch("/ui/api/activity?limit=1", { credentials: "same-origin" }).then(function (response) {
    var token = response.headers.get("X-CSRF-Token");
    if (token) {
      window.pulpOps.csrfToken = token;
    }
  });
});

window.pulpOps.postJSON = function (url, payload) {
  return fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": window.pulpOps.csrfToken || ""
    },
    body: JSON.stringify(payload)
  }).then(function (response) { return response.json(); });
};

// Polling helper for asynchronous Pulp task progress.
window.pulpOps.pollTask = function (href, onUpdate) {
  var timer = setInterval(function () {
    fetch("/ui/api/tasks/detail?href=" + encodeURIComponent(href), {
      credentials: "same-origin"
    })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        onUpdate(body);
        if (["completed", "failed", "canceled"].indexOf(body.state) !== -1) {
          clearInterval(timer);
        }
      });
  }, 2000);
};
