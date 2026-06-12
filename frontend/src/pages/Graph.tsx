import { useState, useCallback } from 'react'
import { Search, Loader2, GitBranch } from 'lucide-react'

interface GraphNode {
  id: string
  type: string
  name: string
  file_path?: string
  line_number?: number
  docstring?: string
}

interface GraphEdge {
  source: string
  target: string
  type: string
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: {
    total_nodes: number
    total_edges: number
    node_types: Record<string, number>
    edge_types: Record<string, number>
  }
}

export default function Graph() {
  const [repoPath, setRepoPath] = useState('')
  const [focusFile, setFocusFile] = useState('')
  const [maxNodes, setMaxNodes] = useState(100)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<GraphNode[]>([])

  const handleLoadGraph = async () => {
    if (!repoPath) return
    setLoading(true)
    setError(null)
    setGraphData(null)

    try {
      const params = new URLSearchParams({ repo_path: repoPath, max_nodes: String(maxNodes) })
      if (focusFile) params.append('focus_file', focusFile)

      const res = await fetch(`/api/graph/data?${params}`)
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || 'Failed to load graph')
      }
      const data = await res.json()
      setGraphData(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async () => {
    if (!repoPath || !searchQuery) return

    try {
      const res = await fetch('/api/graph/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: searchQuery,
          repo_path: repoPath,
          top_k: 10,
        }),
      })
      const data = await res.json()
      setSearchResults(data.nodes || [])
    } catch (err) {
      console.error('Search error:', err)
    }
  }

  const nodeTypeColors: Record<string, string> = {
    function: '#10b981',
    class: '#6366f1',
    method: '#8b5cf6',
    file: '#f59e0b',
    module: '#ec4899',
  }

  const edgeTypeLabels: Record<string, string> = {
    calls: 'calls',
    contains: 'contains',
    imports: 'imports',
    inherits: 'inherits',
  }

  return (
    <div className="space-y-6">
      <div className="bg-white shadow rounded-lg p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4 flex items-center gap-2">
          <GitBranch className="w-5 h-5" />
          Code Property Graph (Graph RAG)
        </h2>

        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Repository Path
              </label>
              <input
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md"
                placeholder="/path/to/repository"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Focus File (optional)
              </label>
              <input
                type="text"
                value={focusFile}
                onChange={(e) => setFocusFile(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md"
                placeholder="src/main.py"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Nodes
              </label>
              <input
                type="number"
                value={maxNodes}
                onChange={(e) => setMaxNodes(parseInt(e.target.value) || 100)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md"
                min={10}
                max={500}
              />
            </div>
          </div>

          <div className="flex gap-4">
            <button
              onClick={handleLoadGraph}
              disabled={loading || !repoPath}
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading...
                </>
              ) : (
                'Load Graph'
              )}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 p-4 rounded-md">
          {error}
        </div>
      )}

      {graphData && (
        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-white shadow rounded-lg p-6">
            <h3 className="text-lg font-medium text-gray-900 mb-4">Graph Visualization</h3>
            <div className="border rounded-lg bg-gray-50 h-96 flex items-center justify-center">
              <div className="text-center text-gray-500">
                <GitBranch className="w-12 h-12 mx-auto mb-2 text-gray-300" />
                <p>Graph visualization would render here</p>
                <p className="text-sm">({graphData.nodes.length} nodes, {graphData.edges.length} edges)</p>
                <p className="text-xs mt-2 text-gray-400">
                  For full visualization, run: python main.py graph --repo-path {repoPath} --visualize
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-6">
            <div className="bg-white shadow rounded-lg p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4">Statistics</h3>
              <div className="space-y-3">
                <div className="flex justify-between">
                  <span className="text-gray-500">Total Nodes</span>
                  <span className="font-medium">{graphData.stats.total_nodes}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Total Edges</span>
                  <span className="font-medium">{graphData.stats.total_edges}</span>
                </div>

                <div className="border-t pt-3 mt-3">
                  <div className="text-sm font-medium text-gray-700 mb-2">Node Types</div>
                  {Object.entries(graphData.stats.node_types).map(([type, count]) => (
                    <div key={type} className="flex justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <span
                          className="w-3 h-3 rounded-full"
                          style={{ backgroundColor: nodeTypeColors[type] || '#9ca3af' }}
                        />
                        {type}
                      </span>
                      <span>{count}</span>
                    </div>
                  ))}
                </div>

                <div className="border-t pt-3 mt-3">
                  <div className="text-sm font-medium text-gray-700 mb-2">Edge Types</div>
                  {Object.entries(graphData.stats.edge_types).map(([type, count]) => (
                    <div key={type} className="flex justify-between text-sm">
                      <span>{edgeTypeLabels[type] || type}</span>
                      <span>{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="bg-white shadow rounded-lg p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4">Search Graph</h3>
              <div className="space-y-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-md"
                    placeholder="Search functions, classes..."
                  />
                  <button
                    onClick={handleSearch}
                    className="px-3 py-2 bg-gray-100 rounded-md hover:bg-gray-200"
                  >
                    <Search className="w-4 h-4" />
                  </button>
                </div>

                {searchResults.length > 0 && (
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {searchResults.map((node) => (
                      <div
                        key={node.id}
                        className="p-2 bg-gray-50 rounded text-sm cursor-pointer hover:bg-gray-100"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: nodeTypeColors[node.type] || '#9ca3af' }}
                          />
                          <span className="font-medium">{node.name}</span>
                          <span className="text-gray-400 text-xs">{node.type}</span>
                        </div>
                        {node.file_path && (
                          <div className="text-xs text-gray-500 mt-1 font-mono">
                            {node.file_path}:{node.line_number}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
