import { useState, useEffect } from 'react'
import { Play, Loader2, CheckCircle, XCircle, FileCode } from 'lucide-react'

interface LocalizeResult {
  job_id: string
  status: string
  result?: {
    instance_id: string
    success: boolean
    ranked_files: string[]
    ranked_locations: Array<{
      rank: number
      file_path: string
      function_name?: string
      class_name?: string
      confidence: number
      explanation?: string
    }>
    explanation: string
    root_cause: string
    total_time: number
    total_llm_calls: number
    total_tool_calls: number
    total_tokens: number
  }
  error?: string
}

export default function Localize() {
  const [bugReport, setBugReport] = useState('')
  const [repoPath, setRepoPath] = useState('')
  const [useGraphRag, setUseGraphRag] = useState(true)
  const [multiPass, setMultiPass] = useState(1)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<LocalizeResult | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!jobId || !loading) return

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/localize/${jobId}`)
        const data = await res.json()
        setResult(data)
        if (data.status !== 'running') {
          setLoading(false)
          clearInterval(interval)
        }
      } catch (err) {
        console.error('Poll error:', err)
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [jobId, loading])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setResult(null)

    try {
      const res = await fetch('/api/localize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bug_report: bugReport,
          repo_path: repoPath,
          use_graph_rag: useGraphRag,
          multi_pass: multiPass,
        }),
      })
      const data = await res.json()
      setJobId(data.job_id)
    } catch (err) {
      setLoading(false)
      setResult({ job_id: '', status: 'failed', error: 'Failed to start job' })
    }
  }

  return (
    <div className="space-y-6">
      <div className="bg-white shadow rounded-lg p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Bug Localization</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Bug Report
            </label>
            <textarea
              value={bugReport}
              onChange={(e) => setBugReport(e.target.value)}
              rows={5}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-indigo-500 focus:border-indigo-500"
              placeholder="Describe the bug or paste a bug report..."
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Repository Path
            </label>
            <input
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-indigo-500 focus:border-indigo-500"
              placeholder="/path/to/repository"
              required
            />
          </div>

          <div className="flex gap-6">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useGraphRag}
                onChange={(e) => setUseGraphRag(e.target.checked)}
                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-sm text-gray-700">Enable Graph RAG</span>
            </label>

            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-700">Multi-pass:</label>
              <input
                type="number"
                value={multiPass}
                onChange={(e) => setMultiPass(parseInt(e.target.value) || 1)}
                min={1}
                max={3}
                className="w-16 px-2 py-1 border border-gray-300 rounded-md"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Running...
              </>
            ) : (
              <>
                <Play className="w-4 h-4" />
                Localize Bug
              </>
            )}
          </button>
        </form>
      </div>

      {result && (
        <div className="bg-white shadow rounded-lg p-6">
          <div className="flex items-center gap-2 mb-4">
            {result.status === 'completed' && result.result?.success ? (
              <CheckCircle className="w-5 h-5 text-green-500" />
            ) : result.status === 'failed' ? (
              <XCircle className="w-5 h-5 text-red-500" />
            ) : (
              <Loader2 className="w-5 h-5 animate-spin text-indigo-500" />
            )}
            <h3 className="text-lg font-medium">
              {result.status === 'running'
                ? 'Processing...'
                : result.status === 'completed'
                ? 'Results'
                : 'Error'}
            </h3>
          </div>

          {result.error && (
            <div className="bg-red-50 text-red-700 p-4 rounded-md mb-4">
              {result.error}
            </div>
          )}

          {result.result && (
            <div className="space-y-4">
              <div className="grid grid-cols-4 gap-4 text-sm">
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-gray-500">Time</div>
                  <div className="font-medium">{result.result.total_time.toFixed(1)}s</div>
                </div>
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-gray-500">LLM Calls</div>
                  <div className="font-medium">{result.result.total_llm_calls}</div>
                </div>
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-gray-500">Tool Calls</div>
                  <div className="font-medium">{result.result.total_tool_calls}</div>
                </div>
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-gray-500">Tokens</div>
                  <div className="font-medium">{result.result.total_tokens.toLocaleString()}</div>
                </div>
              </div>

              {result.result.root_cause && (
                <div className="bg-indigo-50 p-4 rounded-md">
                  <h4 className="font-medium text-indigo-900 mb-1">Root Cause Analysis</h4>
                  <p className="text-sm text-indigo-700">{result.result.root_cause}</p>
                </div>
              )}

              <div>
                <h4 className="font-medium text-gray-900 mb-2 flex items-center gap-2">
                  <FileCode className="w-4 h-4" />
                  Ranked Locations
                </h4>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium text-gray-500">Rank</th>
                        <th className="px-3 py-2 text-left font-medium text-gray-500">File</th>
                        <th className="px-3 py-2 text-left font-medium text-gray-500">Function</th>
                        <th className="px-3 py-2 text-left font-medium text-gray-500">Confidence</th>
                        <th className="px-3 py-2 text-left font-medium text-gray-500">Explanation</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {result.result.ranked_locations.slice(0, 10).map((loc) => (
                        <tr key={loc.rank} className="hover:bg-gray-50">
                          <td className="px-3 py-2 font-medium">{loc.rank}</td>
                          <td className="px-3 py-2 font-mono text-xs">{loc.file_path}</td>
                          <td className="px-3 py-2">
                            {loc.class_name ? `${loc.class_name}.` : ''}
                            {loc.function_name || '-'}
                          </td>
                          <td className="px-3 py-2">
                            <span
                              className={`px-2 py-0.5 rounded text-xs font-medium ${
                                loc.confidence >= 0.7
                                  ? 'bg-green-100 text-green-700'
                                  : loc.confidence >= 0.4
                                  ? 'bg-yellow-100 text-yellow-700'
                                  : 'bg-red-100 text-red-700'
                              }`}
                            >
                              {loc.confidence.toFixed(2)}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-gray-500 truncate max-w-xs">
                            {loc.explanation || '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
