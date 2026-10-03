import type { ReactNode } from "react";
import "./globals.css";
import { ViewerSwitch } from "@/components/ViewerSwitch";

export const metadata = { title: "Redactor (local)", description: "Local annotation and evaluation UI" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <strong>Redactor</strong>
          <nav>
            <a href="/documents">Documents</a>
            <a href="/runs">Runs</a>
            <a href="/intake">Intake</a>
            <a href="/review">Review queue</a>
          </nav>
          <ViewerSwitch />
          <span className="local">local only · confidential</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
