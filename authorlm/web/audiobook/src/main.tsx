import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";
import { TryText } from "./TryText";
import { installTypingFocus } from "./typingFocus";
import { mixWithOtherAudio } from "./audioMix";
import { installPageVolume } from "./audioGain";

installTypingFocus();
mixWithOtherAudio();
installPageVolume();

createRoot(document.getElementById("root")!).render(
  <StrictMode>{location.pathname === "/try-text" ? <TryText /> : <App />}</StrictMode>
);
