import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initI18n } from "./i18n";
import { registerServiceWorker } from "./pwa/register";
import { initTv } from "./tv/tv";
import "./tokens.css";
import "./base.css";
import "./ui/ui.css";

// Eerst de taal (bij Engels: de Engelse basiswoordenboeken), dan pas renderen: t() moet bij de
// eerste render al werken
await initI18n();
// De tv-stand (fase 11, stap 9): alleen in de app voor de LG-tv, of als dit toestel hem onthoudt
initTv();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

registerServiceWorker();
