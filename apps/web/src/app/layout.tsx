import type { Metadata } from "next";
import type { ReactNode } from "react";

import "@fontsource/noto-sans-sinhala/400.css";
import "@fontsource/noto-sans-sinhala/600.css";
import "@fontsource/noto-sans-sinhala/700.css";
import "@fontsource/noto-sans-tamil/400.css";
import "@fontsource/noto-sans-tamil/600.css";
import "@fontsource/noto-sans-tamil/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Admin Content Studio | AI Exam Guru",
  description:
    "Grade 5 Scholarship content intelligence and paper publishing operations.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
