import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// The same Geist family, self-hosted to keep the visual foundation available
// without a build-time request to Google Fonts. See fonts/OFL.txt.
const geistSans = localFont({
  src: "./fonts/Geist-Variable.woff2",
  variable: "--font-geist-sans",
  weight: "100 900",
});

const geistMono = localFont({
  src: "./fonts/GeistMono-Variable.woff2",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "Biztek AI Command Center",
  description: "A real-time operational overview: the Biztek AI Command Center.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full dark antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
