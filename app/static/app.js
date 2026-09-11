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

// Polling helper for asynchronous Pulp task progress. The domain is required: the
// detail endpoint validates the href against it, and defaults to "default" otherwise.
window.pulpOps.pollTask = function (domain, href, onUpdate) {
  var timer = setInterval(function () {
    var query =
      "?domain=" + encodeURIComponent(domain) +
      "&href=" + encodeURIComponent(href);
    fetch("/ui/api/tasks/detail" + query, { credentials: "same-origin" })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        onUpdate(result.body);
        // Stop on a terminal state or on any error response: a rejected request
        // (e.g. a domain mismatch) would otherwise poll forever every 2 seconds.
        if (
          !result.ok ||
          ["completed", "failed", "canceled"].indexOf(result.body.state) !== -1
        ) {
          clearInterval(timer);
        }
      })
      .catch(function () {
        clearInterval(timer);
      });
  }, 2000);
};
