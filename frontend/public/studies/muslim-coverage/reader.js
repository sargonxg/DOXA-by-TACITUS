(() => {
  const directory = document.getElementById("articles");
  const criterion = document.getElementById("criterion");
  const outcome = document.getElementById("outcome");
  const search = document.getElementById("search");
  const more = document.getElementById("more");
  const status = document.getElementById("shown");
  const articles = [...directory.querySelectorAll(".register-item")];
  let limit = 8;
  function render() {
    const needle = search.value.trim().toLowerCase();
    const matched = articles.filter((row) => {
      const states = JSON.parse(row.dataset.states);
      const selected = criterion.value
        ? [states[criterion.value]]
        : Object.values(states);
      return (
        (!outcome.value || selected.includes(outcome.value)) &&
        row.dataset.search.toLowerCase().includes(needle)
      );
    });
    const visible = new Set(matched.slice(0, limit));
    for (const article of articles) article.hidden = !visible.has(article);
    status.textContent = matched.length
      ? "Showing " +
        visible.size +
        " of " +
        matched.length +
        " matching articles."
      : "No articles match these filters.";
    more.hidden = visible.size === matched.length;
  }
  for (const control of [criterion, outcome, search])
    control.addEventListener("input", () => {
      limit = 8;
      render();
    });
  more.addEventListener("click", () => {
    limit += 12;
    render();
  });
  document.getElementById("reset").addEventListener("click", () => {
    criterion.value = "";
    outcome.value = "";
    search.value = "";
    limit = 8;
    render();
  });
  for (const button of document.querySelectorAll("[data-open-criterion]"))
    button.addEventListener("click", () => {
      criterion.value = button.dataset.openCriterion;
      outcome.value = "flagged";
      search.value = "";
      limit = 8;
      render();
      directory.scrollIntoView();
      criterion.focus({ preventScroll: true });
    });
  document.documentElement.classList.add("enhanced");
  render();
})();
