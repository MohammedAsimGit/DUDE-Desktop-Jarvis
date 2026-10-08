import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { applyAppearance, loadAppearance } from "./theme";
import "./styles.css";

// Apply the stored appearance before the first paint to avoid flashing the
// wrong theme ("system" resolves against the OS preference here).
applyAppearance(loadAppearance());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
