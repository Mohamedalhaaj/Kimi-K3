import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Kimi Workspace",
  description: "A local-first AI research workspace.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1622" },
  ],
};

/**
 * Applied before first paint so the app never flashes the wrong theme.
 * Kept inline and tiny: a stylesheet cannot read localStorage, and a
 * client component would run after hydration — too late.
 */
const THEME_INIT = `(function(){try{var t=localStorage.getItem("kimi-theme")||"system";var d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" dir="ltr" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      {/*
        suppressHydrationWarning on <body> as well as <html>: browser extensions
        (antivirus, form fillers, page annotators) routinely stamp attributes
        such as `bis_register` or `__processed_<uuid>__` onto <body> before
        React hydrates, which React then reports as a mismatch we did not cause
        and cannot prevent.

        This is narrow on purpose — the flag suppresses warnings for THIS
        element's own attributes only, not for its subtree, so a genuine
        mismatch inside the app is still reported.
      */}
      <body
        suppressHydrationWarning
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
