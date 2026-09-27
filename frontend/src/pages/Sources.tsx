import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError, api, call, idem } from "../api/client";
import { Badge, EmptyState, ErrorPanel, Field, Loading, PageHeader, RoleButton } from "../components/ui";
import { useBrand } from "../lib/brand";
import { useAction } from "../lib/hooks";
import { humanize } from "../lib/status";

const STATUS_TONE = { PENDING: "info", NORMALIZED: "success", FAILED: "danger", UNSUPPORTED: "neutral" } as const;
const STATUS_LABEL = { PENDING: "Processing", NORMALIZED: "Ready", FAILED: "Could not be read", UNSUPPORTED: "Type not supported yet" } as const;

export function SourcesPage() {
  const { brandId } = useBrand();
  const q = useQuery({
    queryKey: ["sources", brandId],
    queryFn: () => call(api.GET("/api/v1/sources", { params: { query: { brand_id: brandId ?? undefined } } })),
    enabled: Boolean(brandId),
    refetchInterval: (query) => (query.state.data?.items.some((s) => s.status === "PENDING") ? 3000 : false),
  });
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const add = useAction(
    (body: { title: string | null; content: string }, key: string) =>
      call(api.POST("/api/v1/sources/text", { body: { brand_id: brandId ?? "", source_type: "TEXT", ...body }, ...idem(key) })),
    {
      invalidate: [["sources", brandId]],
      onSuccess: () => {
        setTitle("");
        setText("");
      },
    },
  );
  function submit(e: FormEvent) {
    e.preventDefault();
    if (text.trim().length < 20) {
      setProblem("Paste some text first.");
      return;
    }
    setProblem(null);
    add.submit({ title: title.trim() || null, content: text });
  }
  const fieldErrors = add.error instanceof ApiError ? add.error.fieldErrors() : {};
  return (
    <>
      <PageHeader title="Sources" description="Articles, notes and transcripts that campaigns are built from. Writers only state facts found in the sources and the research drawn from them." />
      <form className="card form" onSubmit={submit} noValidate aria-labelledby="add-src-h">
        <h2 id="add-src-h">Add text</h2>
        <Field label="Title" htmlFor="s-title">
          <input id="s-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Text" htmlFor="s-text" error={problem ?? fieldErrors["content"]}>
          <textarea id="s-text" rows={6} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        {add.error && !fieldErrors["content"] ? <ErrorPanel title="Not added" error={add.error} onRetry={add.retry} /> : null}
        <RoleButton role="EDITOR" type="submit" className="btn btn-primary" disabled={add.isPending}>
          Add source
        </RoleButton>
        <p className="small muted">Uploading files (PDF, DOCX, Markdown) and importing URLs work through the API today; this screen supports pasted text.</p>
      </form>
      {q.isLoading ? (
        <Loading />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title="No sources yet" />
      ) : (
        <table className="table">
          <caption className="sr-only">Sources</caption>
          <thead>
            <tr>
              <th scope="col">Title</th>
              <th scope="col">Type</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((s) => (
              <tr key={s.id}>
                <td>{s.title ?? s.source_url ?? "Untitled"}</td>
                <td>{humanize(s.source_type)}</td>
                <td>
                  <Badge tone={STATUS_TONE[s.status]}>{STATUS_LABEL[s.status]}</Badge>
                  {s.error_code ? <span className="small muted"> {humanize(s.error_code)}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
