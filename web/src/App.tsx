import { NavLink, Route, Routes } from 'react-router-dom'
import Health from './pages/Health'
import Market from './pages/Market'
import Backtests from './pages/Backtests'

function NotFound() {
  return <div className="muted">页面不存在。</div>
}

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>KEEL</h1>
        <NavLink to="/" end className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
          系统状态
        </NavLink>
        <NavLink to="/market" className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
          行情
        </NavLink>
        <NavLink to="/backtests" className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
          回测
        </NavLink>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Health />} />
          <Route path="/market" element={<Market />} />
          <Route path="/backtests" element={<Backtests />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}
