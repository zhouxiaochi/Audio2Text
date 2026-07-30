import type { Metadata } from "next";
import { Noto_Sans_SC, Playfair_Display } from "next/font/google";
import "@/app/globals.css";

const sans = Noto_Sans_SC({
  display: "swap",
  subsets: ["latin"],
  variable: "--font-sans",
});

const display = Playfair_Display({
  display: "swap",
  subsets: ["latin"],
  variable: "--font-display",
});

export const metadata: Metadata = {
  title: "声记 · Audio2Text",
  description: "将音频转写、翻译并整理为可编辑文档。",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${sans.variable} ${display.variable}`}>{children}</body>
    </html>
  );
}
