import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import "./index.css";
import { GameList } from "./pages/GameList";
import { GameView } from "./pages/GameView";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<GameList />} />
        <Route path="/game/:gameId" element={<GameView />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
