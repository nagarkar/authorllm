import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";
import { TryText } from "./TryText";

createRoot(document.getElementById("root")!).render(
  <StrictMode>{location.pathname === "/try-text" ? <TryText /> : <App />}</StrictMode>
);
