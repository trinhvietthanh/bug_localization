import { useState, useEffect } from 'react'
import { Play, Loader2, BarChart } from 'lucide-react'
import { BarChart as RechartsBarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface Metrics {
  top_1: number
  top_3: number
  top_5: number
  top_10: number
  mrr: number
  map: number
  total_instances: number
  successful_instances: number
}

interface EvalResult {
  job_id: string
  status: string
  metrics?: Metrics
  results?: Array<{
    instance_id: string
    ranked_files: string[]
    ground_truth: string[]
    hit_at_1: boolean
    hit_at_3: boolean
    hit_at_5: boolean
    hit_at_10: boolean
    time: number
  }>
  error?: string
}

interface Project {
  name: string
  bug_count: number
  language: string
}

export default function Evaluate() {
  const [benchmark, setBenchmark] = useState('defects4j')
  const [project, setProject] = useState('Lang')
  const [limit, setLimit] = useState(10)
  const [useGraphRag, setUseGraphRag] = useState(true)
  const [projects, setProjects] = useState<Project[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<EvalResult | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetch(`/api/benchmarks/projects?benchmark=${benchmark}`)
      .then((res) => res.json())
      .then(setProjects)
      .catch(console.error)
  }, [benchmark])

  useEffect(() => {
    if (!jobId || !loading) return

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/evaluate/${jobId}`)
        const data = await res.json()
        setResult(data)
        if (data.status !== 'running') {
          setLoading(false)
          clearInterval(interval)
        }
      } catch (err) {
        console.error('Poll error:', err)
      }
    }, 3000)

    return () => clearInterval(interval)
  }, [jobId, loading])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setResult(null)

    try {
      const res = await fetch('/api/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          benchmark,
          project,
          limit,
          use_graph_rag: useGraphRag,
        }),
      })
      const data = await res.json()
      setJobId(data.job_id)
    } catch (err) {
      setLoading(false)
      setResult({ job_id: '', status: 'failed', error: 'Failed to start evaluation' })
    }
  }

  const chartData = result?.metrics
    ? [
        { name: 'Top-1', value: result.metrics.top_1 * 100 },
        { name: 'Top-3', value: result.metrics.top_3 * 100 },
        { name: 'Top-5', value: result.metrics.top_5 * 100 },
        { name: 'Top-10', value: result.metrics.top_10 * 100 },
        { name: 'MRR', value: result.metrics.mrr * 100 },
        { name: 'MAP', value: result.metrics.map * 100 },
      ]
    : []

  return (
    <div className="space-y-6">
      <div className="bg-white shadow rounded-lg p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Evaluation Metrics</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Benchmark
              </label>
              <select
                value={benchmark}
                onChange={(e) => setBenchmark(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md"
              >
                <option value="defects4j">Defects4J (Java)</option>
                <option value="bugsinpy">BugsInPy (Python)</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Project
              </label>
              <select
                value={project}
                onChange={(e) => setProject(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md"
              >
                {projects.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name} ({p.bug_count} bugs)
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex gap-6">
            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-700">Limit:</label>
              <input
                type="number"
                value={limit}
                onChange={(e) => setLimit(parseInt(e.target.value) || 10)}
                min={1}
                max={100}
                className="w-20 px-2 py-1 border border-gray-300 rounded-md"
              />
            </div>

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useGraphRag}
                onChange={(e) => setUseGraphRag(e.target.checked)}
                className="rounded border-gray-300 text-indigo-600"
              />
              <span className="text-sm text-gray-700">Enable Graph RAG</span>
            </label>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Running Evaluation...
              </>
            ) : (
              <>
                <Play className="w-4 h-4" />
                Run Evaluation
              </>
            )}
          </button>
        </form>
      </div>

      {result && (
        <div className="space-y-6">
          {result.error && (
            <div className="bg-red-50 text-red-700 p-4 rounded-md">
              {result.error}
            </div>
          )}

          {result.metrics && (
            <div className="bg-white shadow rounded-lg p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4 flex items-center gap-2">
                <BarChart className="w-5 h-5" />
                Metrics Summary
              </h3>

              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="bg-gray-50 p-4 rounded-lg">
                  <div className="text-sm text-gray-500">Total Instances</div>
                  <div className="text-2xl font-bold">{result.metrics.total_instances}</div>
                </div>
                <div className="bg-gray-50 p-4 rounded-lg">
                  <div className="text-sm text-gray-500">Successful</div>
                  <div className="text-2xl font-bold text-green-600">
                    {result.metrics.successful_instances}
                  </div>
                </div>
                <div className="bg-gray-50 p-4 rounded-lg">
                  <div className="text-sm text-gray-500">MRR</div>
                  <div className="text-2xl font-bold text-indigo-600">
                    {(result.metrics.mrr * 100).toFixed(1)}%
                  </div>
                </div>
              </div>

              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <RechartsBarChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="name" />
                    <YAxis domain={[0, 100]} unit="%" />
                    <Tooltip formatter={(value: number) => `${value.toFixed(1)}%`} />
                    <Legend />
                    <Bar dataKey="value" fill="#6366f1" name="Score (%)" />
                  </RechartsBarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {result.results && result.results.length > 0 && (
            <div className="bg-white shadow rounded-lg p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4">Per-Instance Results</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium text-gray-500">Instance</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-500">Top-1</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-500">Top-3</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-500">Top-5</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-500">Top-10</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-500">Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {result.results.map((r) => (
                      <tr key={r.instance_id} className="hover:bg-gray-50">
                        <td className="px-3 py-2 font-mono">{r.instance_id}</td>
                        <td className="px-3 py-2">
                          {r.hit_at_1 ? '✅' : '❌'}
                        </td>
                        <td className="px-3 py-2">
                          {r.hit_at_3 ? '✅' : '❌'}
                        </td>
                        <td className="px-3 py-2">
                          {r.hit_at_5 ? '✅' : '❌'}
                        </td>
                        <td className="px-3 py-2">
                          {r.hit_at_10 ? '✅' : '❌'}
                        </td>
                        <td className="px-3 py-2">{r.time.toFixed(1)}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
