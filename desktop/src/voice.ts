// Text-to-speech for Aria. The browser engine uses the Web Speech API (fully
// on-device, no server); piper/cloud engines fetch audio from /tts and play it.
// Engine is server-authoritative, so enabling voice in the chat or in ⚙ both work.
import { api } from "./api";

export function hasBrowserVoice(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function speak(text: string, engine = "browser", rate = 1): void {
  if (!text) return;
  if (engine === "browser") {
    if (!hasBrowserVoice()) return;  // e.g. some Linux WebKitGTK webviews
    const u = new SpeechSynthesisUtterance(text);
    u.rate = rate || 1;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    return;
  }
  api
    .tts(text)
    .then((r) => {
      if (r.ok && r.audio_b64) {
        new Audio(`data:${r.mime ?? "audio/wav"};base64,${r.audio_b64}`).play().catch(() => {});
      }
    })
    .catch(() => {});
}
