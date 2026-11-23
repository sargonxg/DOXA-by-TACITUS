import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";

export const studyRoot = "/studies/muslim-coverage";
type Ref = { quote: string; block_id: string; start: number; end: number };
type Sensitivity = {
  articles: number;
  observed_flags: number;
  if_all_unresolved_flagged: number;
  year_omissions: { flagged: number; articles: number }[];
};
type Article = {
  id: string;
  title: string;
  author: string;
  translators: string[];
  url: string;
  date: string;
  states: Record<string, string>;
  excerpts: {
    criterion: string;
    result: string;
    voice: string;
    ref: Ref;
    origin: string;
  }[];
};
type Study = {
  title: string;
  article_count: number;
  flagged_articles: number;
  flags: number;
  voices: Record<string, number>;
  uncertain_decisions: number;
  uncertain_articles: number;
  units: number;
  decisions: number;
  missing: number;
  candidates: number;
  criteria: Record<
    string,
    {
      lens: string;
      label: string;
      definition: string;
      states: Record<string, number>;
    }
  >;
  frameworks: Record<string, string>;
  state_names: Record<string, string>;
  sensitivity: Record<string, Sensitivity>;
  cases: {
    slug: string;
    trace: {
      decision: string;
      framework: string;
      version: string;
      rule: string;
      model: string;
      run: string;
      source_hash: string;
    };
    heading: string;
    reading: string;
    followup: string;
    ref: Ref;
    context: string;
    origin: string;
    document: string;
    criterion: string;
    result: string;
    voice: string;
    explanation: string | null;
    peer: string | null;
  }[];
  articles: Article[];
  references: [string, string, string, string][];
};

function searchText(value: string) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

function Credit({ article }: { article: Article }) {
  return (
    <p className="w-study-credit">
      <a href={article.url} target="_blank" rel="noreferrer">
        {article.title} <ExternalLink size={12} />
      </a>
      <br />
      {article.author}
      {article.translators.length > 0 &&
        " · Translation by " + article.translators.join(", ")}{" "}
      · {article.date} · Global Voices ·{" "}
      <a
        href="https://creativecommons.org/licenses/by/3.0/"
        target="_blank"
        rel="noreferrer"
      >
        CC BY 3.0
      </a>
    </p>
  );
}

export function StudyReader() {
  const [data, setData] = useState<Study | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState("Findings");
  const [criterion, setCriterion] = useState("");
  const [outcome, setOutcome] = useState("");
  const [search, setSearch] = useState("");
  const [year, setYear] = useState("");
  const [limit, setLimit] = useState(8);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    fetch(studyRoot + "/reader.json", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Unavailable");
        const study: Study = await response.json();
        if (
          !Array.isArray(study.articles) ||
          study.articles.length !== study.article_count ||
          !Array.isArray(study.cases) ||
          !study.sensitivity ||
          !study.cases.every(
            (item) => item.slug && item.trace && study.articles.some((a) => a.id === item.document),
          )
        )
          throw new Error("Incomplete study");
        if (!controller.signal.aborted) setData(study);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [attempt]);
  if (failed)
    return (
      <div className="w-study-body" role="alert">
        The study could not be loaded.{" "}
        <button onClick={() => setAttempt(attempt + 1)}>Try again</button>
      </div>
    );
  if (!data)
    return (
      <p className="w-study-body" role="status">
        Opening the published readings…
      </p>
    );
  const quoted = data.voices.quoted_speaker || 0;
  const reported = data.voices.reported_speaker || 0;
  function setFilters(key: string, state: string, term: string, publicationYear: string) {
    setCriterion(key);
    setOutcome(state);
    setSearch(term);
    setYear(publicationYear);
    setLimit(8);
  }
  const years = [...new Set(data.articles.map((article) => article.date.slice(0, 4)))].sort();
  const needle = searchText(search.trim());
  const filtered = data.articles.filter((article) => {
    const outcomes = criterion
      ? [article.states[criterion]]
      : Object.values(article.states);
    return (
      (!year || article.date.startsWith(year)) &&
      (!outcome || outcomes.includes(outcome)) &&
      searchText([article.title, article.author, ...article.translators].join(" ")).includes(needle)
    );
  });
  return (
    <div className="w-study-body">
      <p className="w-study-kicker">Global Voices · Field study · 2020–2025</p>
      <h2>{data.title}</h2>
      <nav className="w-study-nav" aria-label="Study sections">
        {["Findings", "Read the passages", "All articles", "Method"].map(
          (tab) => (
            <button
              key={tab}
              aria-current={view === tab ? "page" : undefined}
              onClick={() => setView(tab)}
            >
              {tab}
            </button>
          ),
        )}
      </nav>
      {view === "Findings" && (
        <>
          <p className="w-study-lead">
            The model flagged passages in{" "}
            <b>
              {data.flagged_articles} of {data.article_count} articles
            </b>
            . It attributed{" "}
            <b>
              {reported + quoted} of its {data.flags} flagged readings
            </b>{" "}
            to people the articles quoted or reported.
          </p>
          <aside className="w-study-callout">
            <strong>
              One student faced a choice between removing her headscarf and
              risking expulsion.
            </strong>{" "}
            The model flagged the account of unequal treatment. It recorded
            uncertainty about whether the case established a repeated
            institutional practice.{" "}
            <button onClick={() => setView("Read the passages")}>
              Read the cases →
            </button>
          </aside>
          <h3>What the model flagged</h3>
          <p>
            Each bar counts distinct articles out of {data.article_count}.
            Select a criterion to read the articles behind it.
          </p>
          <div className="w-study-charts">
            {Object.entries(data.frameworks).map(([frame, label]) => (
              <section key={frame}>
                <h4>{label}</h4>
                {Object.entries(data.criteria)
                  .filter(([, row]) => row.lens === frame)
                  .map(([key, row]) => (
                    <button
                      className="w-study-chart-row"
                      key={key}
                      onClick={() => {
                        setFilters(key, "flagged", "", "");
                        setView("All articles");
                      }}
                    >
                      <span>
                        {row.label}{" "}
                        <b>
                          {row.states.flagged} / {data.article_count}
                        </b>
                      </span>
                      <i aria-hidden="true">
                        <em
                          style={{
                            width:
                              (100 * row.states.flagged) / data.article_count +
                              "%",
                          }}
                        />
                      </i>
                    </button>
                  ))}
              </section>
            ))}
          </div>
          <p className="w-study-credit">
            Rows may overlap. The model also recorded {data.uncertain_decisions}{" "}
            uncertain decisions in {data.uncertain_articles} articles.
            Independent reviewers have yet to evaluate these readings.
          </p>
        </>
      )}
      {view === "Read the passages" && (
        <section>
          <h3>Six passages worth reading closely</h3>
          <p>
            Each example pairs a model decision with AI-authored commentary. Two
            invite closer review of a possible error.
          </p>
          {data.cases.map((item) => (
            <article className="w-study-case" key={item.slug} id={"doxa-case-" + item.slug}>
              <small>
                Recorded model outcome · {item.result} · {item.criterion}
              </small>
              <h4>{item.heading}</h4>
              <Credit
                article={data.articles.find((a) => a.id === item.document)!}
              />
              <blockquote>{item.ref.quote}</blockquote>
              <small>
                {item.origin}. Recorded voice · {item.voice}.
              </small>
              <p><b>Editorial reading.</b> {item.reading}</p>
              {item.peer && (
                <p>
                  <b>Second reading.</b> {item.peer}.
                </p>
              )}
              <p>
                <b>Review question.</b> {item.followup}
              </p>
              <details>
                <summary>Evidence path and original model record</summary>
                <p><b>Frozen criterion.</b> {item.trace.rule}</p>
                <p>{item.trace.framework} · {item.trace.version}</p>
                <blockquote>{item.context}</blockquote>
                <p>
                  {item.explanation ||
                    "The saved decision contains an outcome code without a written rationale."}
                </p>
                <small>
                  {item.ref.block_id} · characters {item.ref.start}–
                  {item.ref.end}
                </small>
                <p className="w-study-trace">
                  Decision {item.trace.decision} · run {item.trace.run} · {item.trace.model}.
                  Source version SHA-256 {item.trace.source_hash}.
                </p>
                <a href={studyRoot + "/index.html#case-" + item.slug}>Open this case in the standalone reader</a>
              </details>
            </article>
          ))}
        </section>
      )}
      {view === "All articles" && (
        <section>
          <h3>Explore all {data.article_count} articles</h3>
          <div className="w-study-controls">
            <label>
              Find a title or credit
              <input
                type="search"
                value={search}
                onChange={(e) => setFilters(criterion, outcome, e.target.value, year)}
              />
            </label>
            <label>
              Criterion
              <select
                value={criterion}
                onChange={(e) => setFilters(e.target.value, outcome, search, year)}
              >
                <option value="">Any criterion</option>
                {Object.entries(data.criteria).map(([id, r]) => (
                  <option key={id} value={id}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Article reading
              <select
                value={outcome}
                onChange={(e) => setFilters(criterion, e.target.value, search, year)}
              >
                <option value="">Any reading</option>
                {Object.entries(data.state_names).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Publication year
              <select value={year} onChange={(e) => setFilters(criterion, outcome, search, e.target.value)}>
                <option value="">Any year</option>
                {years.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
            <button onClick={() => setFilters("", "", "", "")}>
              Clear filters
            </button>
          </div>
          <p role="status">
            {filtered.length
              ? "Showing " +
                Math.min(limit, filtered.length) +
                " of " +
                filtered.length +
                " matching articles."
              : "No articles match these filters."}
          </p>
          {filtered.slice(0, limit).map((article) => (
            <details key={article.id} className="w-study-register">
              <summary>{article.title}</summary>
              <Credit article={article} />
              {Object.entries(article.states).map(([key, state]) => (
                <div className="w-study-decision" key={key}>
                  <b>{data.criteria[key].label}</b> · {data.state_names[state]}
                  {article.excerpts
                    .filter((e) => e.criterion === key)
                    .map((e, i) => (
                      <div key={i}>
                        <blockquote>{e.ref.quote}</blockquote>
                        <small>
                          {e.result} · {e.voice} · {e.origin}. {e.ref.block_id},
                          characters {e.ref.start}–{e.ref.end}.
                        </small>
                      </div>
                    ))}
                </div>
              ))}
            </details>
          ))}
          {limit < filtered.length && (
            <button
              className="w-study-more"
              onClick={() => setLimit(limit + 12)}
            >
              Show more articles
            </button>
          )}
        </section>
      )}
      {view === "Method" && (
        <section>
          <h3>What the figures can tell you</h3>
          <p>
            Of 5,000 articles in the archive, {data.candidates} eligible
            English-edition development articles mentioned Muslim, Muslims,
            Islamophobia or Islamophobic in the body. A fixed hash selected ten
            distinct stories per year before the model outcomes were opened. A
            personal name can match this search. A story using different terms
            can be missed.
          </p>
          <p>
            The original Gemma run assessed {data.units} source units against
            six criteria. The archive retains {data.decisions} of {data.decisions + data.missing} expected
            decisions. For each criterion, an article with any flag counts once.
            For other articles the order is missing decision, uncertainty,
            checked without a flag, then all units outside the criterion.
          </p>
          <p>
            The study describes one publisher and an equal number of articles
            per year. Estimating prevalence, comparing publishers or measuring
            accuracy requires a different sample and independent human
            annotations.
          </p>
          <h3 id="doxa-study-sensitivity">How much do unresolved readings matter?</h3>
          <p>Compare observed flags with the maximum if every unresolved article were flagged.
            The last column shows the range obtained by omitting one publication year at a time.</p>
          <div className="w-study-table" role="region" aria-labelledby="doxa-study-sensitivity" tabIndex={0}>
            <table>
              <thead><tr>
                <th scope="col">Criterion</th>
                <th scope="col">Observed</th>
                <th scope="col">All unresolved flagged</th>
                <th scope="col">One year omitted</th>
              </tr></thead>
              <tbody>
                {Object.entries(data.sensitivity).map(([key, record]) => {
                  const low = record.year_omissions[0];
                  const high = record.year_omissions.at(-1);
                  return (
                    <tr key={key}>
                      <th scope="row">{data.criteria[key].label}</th>
                      <td>{record.observed_flags}/{record.articles}</td>
                      <td>{record.if_all_unresolved_flagged}/{record.articles}</td>
                      <td>{low && high ? `${low.flagged}/${low.articles} to ${high.flagged}/${high.articles}` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {data.references.map(([kind, title, meaning, url]) => (
            <p key={url}>
              <b>{kind}.</b> {meaning}{" "}
              <a href={url} target="_blank" rel="noreferrer">
                {title}
              </a>
            </p>
          ))}
        </section>
      )}
      <div className="w-study-downloads">
        <a href={studyRoot + "/report.pdf"} download>
          Download PDF
        </a>
        <a href={studyRoot + "/evidence.xlsx"} download>
          Download workbook
        </a>
        <a href={studyRoot + "/sources.zip"} download>
          Download 60 texts and decisions
        </a>
        <a href={studyRoot + "/evidence.jsonld"} download>
          Download evidence graph
        </a>
      </div>
    </div>
  );
}
