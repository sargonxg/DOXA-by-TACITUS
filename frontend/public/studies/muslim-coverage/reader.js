(() => {
  const directory = document.getElementById("articles");
  const criterion = document.getElementById("criterion");
  const outcome = document.getElementById("outcome");
  const year = document.getElementById("year");
  const search = document.getElementById("search");
  const more = document.getElementById("more");
  const status = document.getElementById("shown");
  const normalized = (value) =>
    value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  const articles = [...directory.querySelectorAll(".register-item")].map(
    (element) => ({
      element,
      states: JSON.parse(element.dataset.states),
      year: element.dataset.year,
      text: normalized(element.dataset.search),
    }),
  );
  let limit = 8;
  function render() {
    const needle = normalized(search.value.trim());
    const matched = articles.filter((row) => {
      const selected = criterion.value
        ? [row.states[criterion.value]]
        : Object.values(row.states);
      return (
        (!year.value || row.year === year.value) &&
        (!outcome.value || selected.includes(outcome.value)) &&
        row.text.includes(needle)
      );
    });
    const visible = new Set(matched.slice(0, limit));
    for (const article of articles) article.element.hidden = !visible.has(article);
    status.textContent = matched.length
      ? "Showing " +
        visible.size +
        " of " +
        matched.length +
        " matching articles."
      : "No articles match these filters.";
    more.hidden = visible.size === matched.length;
  }
  for (const control of [criterion, outcome, year, search])
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
    year.value = "";
    search.value = "";
    limit = 8;
    render();
  });
  for (const button of document.querySelectorAll("[data-open-criterion]"))
    button.addEventListener("click", () => {
      criterion.value = button.dataset.openCriterion;
      outcome.value = "flagged";
      year.value = "";
      search.value = "";
      limit = 8;
      render();
      directory.scrollIntoView();
      criterion.focus({ preventScroll: true });
    });
  document.documentElement.classList.add("enhanced");
  render();
})();
