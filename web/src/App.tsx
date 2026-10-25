import { Navigate, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import Join from "./pages/Join";
import Table from "./pages/Table";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/table/:sid" element={<Table />} />
      <Route path="/join/:sid/:seat" element={<Join />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
