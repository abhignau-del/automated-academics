import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthGate, parseResetHash, ResetPage } from "./auth";
import { parsePublicHash, PublicTimetable } from "./PublicTimetable";
import "./styles.css";

// A published link (#/p/<token>) opens a read-only timetable for anyone; everything else is the app.
const published = parsePublicHash(window.location.hash);
const resetToken = parseResetHash(window.location.hash);
window.addEventListener("hashchange", () => {
  const h = window.location.hash;
  if (!!parsePublicHash(h) !== !!published || !!parseResetHash(h) !== !!resetToken) window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {resetToken ? (
      <ResetPage token={resetToken} />
    ) : published ? (
      <PublicTimetable token={published.token} />
    ) : (
      <AuthGate>
        <App />
      </AuthGate>
    )}
  </StrictMode>,
);
