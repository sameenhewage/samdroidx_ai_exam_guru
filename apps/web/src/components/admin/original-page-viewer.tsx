"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "react-aria-components";

import type { ReviewLanguage } from "@/lib/review-language";
import { cn } from "@/lib/utils";

export const viewerButtonClass = "inline-flex min-h-10 cursor-pointer items-center justify-center rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-950 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-700";

const english = {
  original: "Original page", previewLoading: "Loading original page…",
  previewError: "The original page could not be loaded. Retry this page; the PDF will not be downloaded automatically.",
  imageRetry: "Try image again", zoom: "Page zoom", zoomIn: "Zoom in", zoomOut: "Zoom out", zoomReset: "Reset zoom", fitPage: "Fit page",
  first: "First page", previous: "Previous page", next: "Next page", last: "Last page", pageNumber: "Page number", of: "of", go: "Go to page",
  invalidPage: "Enter a page number from 1 to {total}.", pageCountUnknown: "Page count is not available yet. You can inspect page 1 or retry the document details.",
  view: "View PDF", download: "Download original PDF", retryDetails: "Retry document details",
};
const sinhala: typeof english = {
  original: "මුල් පිටුව", previewLoading: "මුල් පිටුව පූරණය වෙමින් පවතී…",
  previewError: "මුල් පිටුව පූරණය කළ නොහැකි විය. මෙම පිටුව නැවත ලබාගන්න. PDF ගොනුව ස්වයංක්‍රීයව බාගත නොවේ.",
  imageRetry: "පිටුවේ රූපය නැවත පූරණය කරන්න", zoom: "පිටුවේ විශාලත්වය", zoomIn: "විශාල කරන්න", zoomOut: "කුඩා කරන්න", zoomReset: "මුල් විශාලත්වයට යන්න", fitPage: "පිටුවට ගළපන්න",
  first: "පළමු පිටුව", previous: "පෙර පිටුව", next: "ඊළඟ පිටුව", last: "අවසාන පිටුව", pageNumber: "පිටු අංකය", of: "/", go: "පිටුවට යන්න",
  invalidPage: "1 සිට {total} දක්වා පිටු අංකයක් ඇතුළත් කරන්න.", pageCountUnknown: "පිටු ගණන තවම නොමැත. පළමු පිටුව බලන්න හෝ ලේඛනයේ තොරතුරු නැවත ලබාගන්න.",
  view: "PDF බලන්න", download: "මුල් PDF ගොනුව බාගන්න", retryDetails: "ලේඛනයේ තොරතුරු නැවත ලබාගන්න",
};
export function sourceViewerCopy(language: ReviewLanguage) { return language === "si" ? sinhala : english; }

type Labels = Pick<typeof english, "original" | "previewLoading" | "previewError" | "imageRetry" | "zoom" | "zoomIn" | "zoomOut" | "zoomReset"> & { fitPage?: string };
type Props = {
  documentId: string; pageNumber: number; previewUrl?: string; language?: ReviewLanguage;
  labels?: Partial<Labels>; onReady?: (ready: boolean) => void; className?: string;
  expectedDimensions?: { width: number; height: number }; initialFit?: boolean;
};

export function OriginalPageViewer(props: Props) {
  const url = props.previewUrl ?? `/api/v1/admin/materials/${encodeURIComponent(props.documentId)}/pages/${props.pageNumber}/image`;
  return <PageImage key={`${props.documentId}:${props.pageNumber}:${url}`} {...props} url={url} />;
}

function PageImage({ pageNumber, language = "en", labels, onReady, className, expectedDimensions, initialFit = false, url }: Props & { url: string }) {
  const copy = { ...sourceViewerCopy(language), ...labels };
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [fit, setFit] = useState(initialFit);
  const [fitZoom, setFitZoom] = useState(100);
  const root = useRef<HTMLElement>(null);
  const header = useRef<HTMLElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const alive = useRef(true);
  const measure = useCallback(() => {
    const element = image.current, container = root.current;
    if (!element?.naturalWidth || !element.naturalHeight || !container) return;
    const width = container.clientWidth - 16;
    const height = container.clientHeight - (header.current?.offsetHeight ?? 0) - 16;
    if (width > 0 && height > 0) setFitZoom(Math.max(1, Math.min(100, Math.floor(height * element.naturalWidth / (width * element.naturalHeight) * 100))));
  }, []);
  useEffect(() => {
    alive.current = true;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (root.current) observer?.observe(root.current);
    if (header.current) observer?.observe(header.current);
    window.addEventListener("resize", measure);
    return () => { alive.current = false; observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, [measure]);
  const percent = fit ? fitZoom : zoom;
  function changeZoom(value: number) { setFit(false); setZoom(Math.max(50, Math.min(200, value))); }
  return (
    <section ref={root} aria-label={copy.original} data-original-page-viewer="" className={cn("min-h-0 overflow-auto overscroll-contain rounded-lg border border-slate-300 bg-slate-100", className)} tabIndex={0}>
      <header ref={header} className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-slate-300 bg-white p-2">
        <h2 className="mr-auto font-semibold">{copy.original}</h2>
        <Button aria-label={copy.zoomOut} className={viewerButtonClass} isDisabled={percent <= 50} onPress={() => changeZoom(percent - 25)}>−</Button>
        <output aria-label={copy.zoom} className="min-w-12 text-center text-sm">{percent}%</output>
        <Button aria-label={copy.zoomIn} className={viewerButtonClass} isDisabled={percent >= 200} onPress={() => changeZoom(percent + 25)}>+</Button>
        <Button className={viewerButtonClass} onPress={() => { measure(); setFit(true); }}>{copy.fitPage}</Button>
        <Button aria-label={copy.zoomReset} className={viewerButtonClass} onPress={() => { setFit(false); setZoom(100); }}>
          <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-2L20 8M4 16l2.3 3A7 7 0 0 0 18 17" /></svg>
        </Button>
      </header>
      {state === "loading" && <p className="p-3 text-sm" role="status">{copy.previewLoading}</p>}
      {state === "error" && <div className="m-3 rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950" role="alert">
        <p>{copy.previewError}</p>
        <Button className={cn(viewerButtonClass, "mt-2")} onPress={() => { setState("loading"); onReady?.(false); setAttempt(value => value + 1); }}>{copy.imageRetry}</Button>
      </div>}
      <div className="mx-auto p-2" style={{ width: `${percent}%` }}>
        <Image ref={image} key={attempt} alt={`${copy.original} ${pageNumber}`} src={url} width={expectedDimensions?.width ?? 1000} height={expectedDimensions?.height ?? 1414}
          className="block h-auto w-full max-w-none bg-white" loading="eager" unoptimized referrerPolicy="no-referrer"
          onError={() => { if (alive.current) { setState("error"); onReady?.(false); } }}
          onLoad={event => {
            if (!alive.current) return;
            const target = event.currentTarget;
            const valid = !expectedDimensions || (target.naturalWidth === expectedDimensions.width && target.naturalHeight === expectedDimensions.height);
            setState(valid ? "ready" : "error"); onReady?.(valid); if (valid) measure();
          }} />
      </div>
    </section>
  );
}
