import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { Bug, BarChart3, GitBranch } from 'lucide-react'
import Localize from './pages/Localize'
import Evaluate from './pages/Evaluate'
import Graph from './pages/Graph'

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-4 sm:px-6 lg:px-8 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bug className="w-8 h-8 text-indigo-600" />
            <h1 className="text-xl font-bold text-gray-900">Bug Localization</h1>
          </div>
          <nav className="flex gap-4">
            <NavLink
              to="/"
              className={({ isActive }) =>
                `px-3 py-2 rounded-md text-sm font-medium ${
                  isActive
                    ? 'bg-indigo-100 text-indigo-700'
                    : 'text-gray-600 hover:bg-gray-100'
                }`
              }
            >
              <span className="flex items-center gap-1">
                <Bug className="w-4 h-4" />
                Localize
              </span>
            </NavLink>
            <NavLink
              to="/evaluate"
              className={({ isActive }) =>
                `px-3 py-2 rounded-md text-sm font-medium ${
                  isActive
                    ? 'bg-indigo-100 text-indigo-700'
                    : 'text-gray-600 hover:bg-gray-100'
                }`
              }
            >
              <span className="flex items-center gap-1">
                <BarChart3 className="w-4 h-4" />
                Evaluate
              </span>
            </NavLink>
            <NavLink
              to="/graph"
              className={({ isActive }) =>
                `px-3 py-2 rounded-md text-sm font-medium ${
                  isActive
                    ? 'bg-indigo-100 text-indigo-700'
                    : 'text-gray-600 hover:bg-gray-100'
                }`
              }
            >
              <span className="flex items-center gap-1">
                <GitBranch className="w-4 h-4" />
                Graph
              </span>
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-4 py-8 sm:px-6 lg:px-8">
        {children}
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Localize />} />
          <Route path="/evaluate" element={<Evaluate />} />
          <Route path="/graph" element={<Graph />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
