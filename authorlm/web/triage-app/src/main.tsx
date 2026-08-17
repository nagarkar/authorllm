import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@glideapps/glide-data-grid/dist/index.css";
import "./styles.css";
import { AppRoot } from "./transport";

createRoot(document.getElementById("root")!).render(
  <StrictMode><AppRoot /></StrictMode>,
);
