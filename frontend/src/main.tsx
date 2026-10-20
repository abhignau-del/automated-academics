import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthGate } from "./auth";
import { parsePublicHash, PublicTimetable } from "./PublicTimetable";
import "./styles.css";

// A published link (#/p/<token>) opens a read-only timetable for anyone; everything else is the app.
const published = parsePublicHash(window.location.hash);
window.addEventListener("hashchange", () => { if (!!parsePublicHash(window.location.hash) !== !!published) window.location.reload(); });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {published ? (
      <PublicTimetable token={published.token} />
    ) : (
      <AuthGate>
        <App />
      </AuthGate>
    )}
  </StrictMode>,
);
