export type ReviewLanguage = "en" | "si";
export const reviewLanguageKey = "exam-guru:review-language:v1";

export function savedReviewLanguage(): ReviewLanguage | null {
  try {
    const language = window.localStorage.getItem(reviewLanguageKey);
    return language === "si" || language === "en" ? language : null;
  } catch {
    return null;
  }
}

export function subscribeReviewLanguage(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}
