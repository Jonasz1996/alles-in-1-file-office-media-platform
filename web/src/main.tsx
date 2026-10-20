import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Beheer } from "./Beheer";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.pathname.startsWith("/beheer") ? <Beheer /> : <App />}
  </StrictMode>,
);
