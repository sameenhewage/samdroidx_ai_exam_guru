import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { OriginalPageViewer } from "./original-page-viewer";
import { SourceDocumentViewer } from "./source-document-viewer";

const documentId = "00000000-0000-0000-0000-000000000981";
const props = { documentId, title: "Immutable book.pdf", pageCount: 371, language: "en" as const, onLanguageChange: vi.fn(), onRetryDetails: vi.fn() };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("keeps only one requested page mounted across direct jumps and navigation", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const view = render(<SourceDocumentViewer {...props} />);
  const source = (page: number) => `${window.location.origin}/api/v1/admin/materials/${documentId}/pages/${page}/image`;
  expect(screen.getAllByRole("img")).toHaveLength(1);
  expect(screen.getByRole("img")).toHaveAttribute("src", source(1));
  expect(screen.getByRole("button", { name: "First page" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Last page" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", source(371));
  expect(screen.getByRole("button", { name: "Last page" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "185" } });
  fireEvent.submit(screen.getByRole("spinbutton").closest("form")!);
  expect(screen.getByRole("img")).toHaveAttribute("src", source(185));
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", source(184));
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", source(185));
  fireEvent.click(screen.getByRole("button", { name: "First page" }));
  expect(screen.getByRole("img")).toHaveAttribute("src", source(1));
  expect(screen.getAllByRole("img")).toHaveLength(1);
  expect(view.container.querySelector("iframe, object, embed, a[target='_blank']")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it.each(["0", "372", "1.5", "-1", "", "2147483647"])("rejects an invalid jump %s without replacing the original page", value => {
  render(<SourceDocumentViewer {...props} />);
  fireEvent.change(screen.getByRole("spinbutton"), { target: { value } });
  fireEvent.submit(screen.getByRole("spinbutton").closest("form")!);
  expect(screen.getByRole("alert")).toHaveTextContent("Enter a page number from 1 to 371");
  expect(screen.getByRole("img")).toHaveAttribute("src", `${window.location.origin}/api/v1/admin/materials/${documentId}/pages/1/image`);
});

it("keeps page 1 available without inventing a page count or triggering extraction", () => {
  const retry = vi.fn();
  render(<SourceDocumentViewer {...props} pageCount={null} onRetryDetails={retry} />);
  expect(screen.getByRole("img")).toHaveAttribute("src", `${window.location.origin}/api/v1/admin/materials/${documentId}/pages/1/image`);
  expect(screen.getByRole("spinbutton")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Last page" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry document details" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("retries only the current image and never falls back to a raw PDF", async () => {
  const ready = vi.fn();
  render(<OriginalPageViewer documentId={documentId} pageNumber={185} onReady={ready} />);
  const image = screen.getByRole("img");
  fireEvent.error(image);
  expect(screen.getByRole("alert")).toHaveTextContent("PDF will not be downloaded automatically");
  expect(ready).toHaveBeenLastCalledWith(false);
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Try image again" }));
  const retried = screen.getByRole("img");
  expect(retried).not.toBe(image);
  expect(retried.getAttribute("src")).toBe(image.getAttribute("src"));
  await act(async () => fireEvent.load(retried));
  expect(ready).toHaveBeenLastCalledWith(true);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("fits the complete page and zooms without requesting another image", async () => {
  render(<OriginalPageViewer documentId={documentId} pageNumber={1} />);
  const image = screen.getByRole("img");
  const root = screen.getByRole("region", { name: "Original page" });
  Object.defineProperty(root, "clientWidth", { configurable: true, value: 616 });
  Object.defineProperty(root, "clientHeight", { configurable: true, value: 500 });
  Object.defineProperty(root.querySelector("header"), "offsetHeight", { configurable: true, value: 56 });
  Object.defineProperty(image, "naturalWidth", { configurable: true, value: 1000 });
  Object.defineProperty(image, "naturalHeight", { configurable: true, value: 1400 });
  await act(async () => fireEvent.load(image));
  fireEvent.click(screen.getByRole("button", { name: "Fit page" }));
  expect(screen.getByLabelText("Page zoom")).toHaveTextContent("50%");
  expect(image.parentElement).toHaveStyle({ width: "50%" });
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByLabelText("Page zoom")).toHaveTextContent("75%");
  fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
  expect(screen.getByLabelText("Page zoom")).toHaveTextContent("100%");
  expect(screen.getByRole("img")).toBe(image);
});

it("keeps language changes separate from page and zoom identity", () => {
  const view = render(<SourceDocumentViewer {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Last page" }));
  fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  const image = screen.getByRole("img");
  view.rerender(<SourceDocumentViewer {...props} language="si" />);
  expect(screen.getByRole("img", { name: "මුල් පිටුව 371" })).toBe(image);
  expect(screen.getByLabelText("පිටුවේ විශාලත්වය")).toHaveTextContent("125%");
  expect(screen.getByRole("spinbutton", { name: "පිටු අංකය" })).toHaveValue(371);
  expect(within(screen.getByRole("navigation")).getByRole("button", { name: "අවසාන පිටුව" })).toBeDisabled();
});

it("resets image failure and page identity when opening a different document", () => {
  const view = render(<SourceDocumentViewer {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Last page" }));
  fireEvent.error(screen.getByRole("img"));
  const nextId = "00000000-0000-0000-0000-000000000982";
  view.rerender(<SourceDocumentViewer {...props} documentId={nextId} />);
  expect(screen.getByRole("img")).toHaveAttribute("src", `${window.location.origin}/api/v1/admin/materials/${nextId}/pages/1/image`);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
