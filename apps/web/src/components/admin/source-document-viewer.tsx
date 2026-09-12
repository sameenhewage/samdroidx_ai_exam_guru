"use client";

import { useId, useState, type FormEvent } from "react";
import { Button, Form } from "react-aria-components";

import type { ReviewLanguage } from "@/lib/review-language";

import { OriginalPageViewer, sourceViewerCopy, viewerButtonClass } from "./original-page-viewer";

type Props = {
  documentId: string; title: string; pageCount: number | null; language: ReviewLanguage;
  onLanguageChange: (language: ReviewLanguage) => void; onRetryDetails: () => void;
};

export function SourceDocumentViewer(props: Props) {
  return <DocumentPages key={props.documentId} {...props} />;
}

function DocumentPages({ documentId, title, pageCount, language, onLanguageChange, onRetryDetails }: Props) {
  const copy = sourceViewerCopy(language);
  const [page, setPage] = useState(1);
  const [input, setInput] = useState("1");
  const [invalid, setInvalid] = useState(false);
  const inputId = useId();
  const total = typeof pageCount === "number" && Number.isSafeInteger(pageCount) && pageCount > 0 && pageCount <= 2147483646 ? pageCount : null;
  function navigate(number: number) {
    if (!total || !Number.isSafeInteger(number) || number < 1 || number > total) { setInvalid(true); return; }
    setPage(number); setInput(String(number)); setInvalid(false);
  }
  function jump(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!/^\d+$/.test(input)) { setInvalid(true); return; }
    navigate(Number(input));
  }
  return (
    <section title={`Original PDF: ${title}`} aria-label={`Original PDF: ${title}`} className="mt-4 flex h-[75dvh] min-h-[28rem] flex-col gap-2" lang={language}>
      <nav aria-label="PDF page navigation" className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-slate-300 bg-white p-2">
        <Button className={viewerButtonClass} isDisabled={!total || page === 1} onPress={() => navigate(1)}>{copy.first}</Button>
        <Button className={viewerButtonClass} isDisabled={!total || page === 1} onPress={() => navigate(page - 1)}>{copy.previous}</Button>
        <Form className="flex items-center gap-2" validationBehavior="aria" onSubmit={jump}>
          <label className="sr-only" htmlFor={inputId}>{copy.pageNumber}</label>
          <input id={inputId} aria-invalid={invalid || undefined} className="min-h-10 w-20 rounded-lg border border-slate-400 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-amber-600" type="number" inputMode="numeric" min={1} max={total ?? undefined} step={1} value={input} disabled={!total}
            onChange={event => { setInput(event.currentTarget.value); setInvalid(false); }} />
          <span className="whitespace-nowrap text-sm">{copy.of} {total ?? "—"}</span>
          <Button className={viewerButtonClass} isDisabled={!total} type="submit">{copy.go}</Button>
        </Form>
        <Button className={viewerButtonClass} isDisabled={!total || page >= total} onPress={() => navigate(page + 1)}>{copy.next}</Button>
        <Button className={viewerButtonClass} isDisabled={!total || page >= total} onPress={() => navigate(total!)}>{copy.last}</Button>
        <select className="ml-auto min-h-10 rounded-lg border border-slate-300 bg-white px-2 text-sm" aria-label="Viewer language / භාෂාව" value={language}
          onChange={event => onLanguageChange(event.currentTarget.value === "si" ? "si" : "en")}>
          <option value="en">English</option><option value="si">සිංහල</option>
        </select>
      </nav>
      {invalid && <p role="alert" className="shrink-0 rounded border border-amber-400 bg-amber-50 p-2 text-sm">{copy.invalidPage.replace("{total}", String(total ?? "—"))}</p>}
      {!total && <div className="shrink-0 rounded border border-amber-400 bg-amber-50 p-2 text-sm"><p role="status">{copy.pageCountUnknown}</p><Button className={viewerButtonClass} onPress={onRetryDetails}>{copy.retryDetails}</Button></div>}
      <OriginalPageViewer documentId={documentId} pageNumber={page} language={language} className="flex-1" initialFit />
    </section>
  );
}
