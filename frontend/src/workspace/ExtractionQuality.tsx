import { useState } from "react";
import { request } from "../api";
import { list, message, record } from "./client";
import { Badge, Button, ErrorMessage, human, number, Spinner } from "./ui";
import type { Item, SourceDocument } from "./types";

export function ExtractionQuality({ document }: { document: SourceDocument }) {
  const summary = record(record(record(document).metadata).extraction_manifest);
  const [ledger, setLedger] = useState<Item | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function load(next = 0) {
    setLoading(true);
    setError("");
    try {
      setLedger(
        await request<Item>(
          `/documents/${encodeURIComponent(document.id)}/extraction?offset=${next}&limit=100`,
        ),
      );
      setOffset(next);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoading(false);
    }
  }
  return (
    <details
      className="w-details w-source-quality"
      open={summary.needs_review === true}
    >
      <summary>Source extraction quality · separate from analysis</summary>
      <p>
        {summary.source_coverage_complete === true
          ? "The extraction ledger reports complete coverage of its declared text scope. This does not establish document understanding or semantic accuracy."
          : summary.source_coverage_complete === false
            ? "Source extraction is incomplete or needs review. Analyzing extracted text does not recover missing or unreadable source material."
            : "A complete extraction-coverage ledger was not supplied for this source."}
      </p>
      <p className="w-footnote">
        Visual content, OCR uncertainty and declared exclusions remain separate
        from model decisions on the extracted text.
      </p>
      {list<string>(document.extraction_warnings).map((warning, index) => (
        <p key={index}>{warning}</p>
      ))}
      {list<unknown>(summary.exclusions_preview).map((value, index) => (
        <p key={index}>
          {typeof value === "string"
            ? value
            : String(
                record(value).reason ||
                  record(value).description ||
                  JSON.stringify(value),
              )}
        </p>
      ))}
      <Button variant="outline" disabled={loading} onClick={() => void load()}>
        Inspect page extraction ledger
      </Button>
      {loading && <Spinner text="Loading at most 100 page records…" />}
      {error && <ErrorMessage text={error} />}
      {ledger && (
        <div className="w-extraction-ledger">
          {ledger.status === "not_recorded" ? (
            <p>
              This historical source has no recorded page extraction ledger. No
              coverage value has been invented.
            </p>
          ) : (
            <>
              <p>
                {number(ledger.total_pages)} recorded PDF pages · offset{" "}
                {number(offset)} · at most 100 loaded
              </p>
              {list<Item>(ledger.pages).length ? (
                <table>
                  <caption>
                    Extraction method and completeness for the frozen source
                    version.
                  </caption>
                  <thead>
                    <tr>
                      <th>Page</th>
                      <th>Method</th>
                      <th>Text</th>
                      <th>Review</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list<Item>(ledger.pages).map((page, index) => (
                      <tr key={String(page.page || index)}>
                        <th scope="row">
                          {String(page.page || index + offset + 1)}
                        </th>
                        <td>{human(page.method)}</td>
                        <td>{number(page.characters)} characters</td>
                        <td>
                          <Badge tone={page.needs_review ? "amber" : ""}>
                            {human(
                              page.completeness ||
                                (page.needs_review
                                  ? "needs_review"
                                  : "not_supplied"),
                            )}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p>
                  This format has no recorded PDF page tree. Its text extraction
                  scope and exclusions remain in the provenance.
                </p>
              )}
              <div className="w-case-actions">
                <Button
                  variant="quiet"
                  disabled={loading || offset === 0}
                  onClick={() => void load(Math.max(0, offset - 100))}
                >
                  Previous 100 pages
                </Button>
                <Button
                  variant="quiet"
                  disabled={loading || typeof ledger.next_offset !== "number"}
                  onClick={() => void load(Number(ledger.next_offset))}
                >
                  Next 100 pages
                </Button>
              </div>
            </>
          )}
        </div>
      )}
      {Object.keys(summary).length > 0 && (
        <details className="w-details">
          <summary>Technical extraction provenance</summary>
          <pre>
            {JSON.stringify(
              ledger
                ? {
                    ...ledger,
                    pages: `${list(ledger.pages).length} page records displayed above`,
                  }
                : summary,
              null,
              2,
            )}
          </pre>
        </details>
      )}
    </details>
  );
}
