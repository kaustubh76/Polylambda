import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, useApi } from '../api/client'
import { useInViewOnce } from '../lib/motion'
import { useColors } from '../components/Theme'
import { int } from '../lib/format'
import { Async, Caveat, Panel, Section, SourceTag, Stat } from '../components/ui'

// The HF dataset backbone (moose-code/polymarket-onchain-v1) that powers the whole stack — finally
// surfaced. Everything here is computed from the on-chain condition + market_data tables.
export function HfDataset() {
  const { C } = useColors()
  const q = useApi(api.hfOverview, [])
  const [barRef, barIn] = useInViewOnce<HTMLDivElement>()
  return (
    <Section id="hfdata" kicker="the data backbone · Hugging Face"
      title="HF dataset — the on-chain substrate"
      subtitle="Every base rate, σ prior and joinable market in this dashboard is derived from the moose-code/polymarket-onchain-v1 dataset (1.17B fills, 1.1M markets). This is that backbone made visible — resolution ground truth, market coverage, and category structure, computed straight from the on-chain tables."
      right={q.data?.source && <SourceTag source={q.data.source === 'live' ? 'live' : 'published'} />}>
      <Async q={q}>{(d) => {
        const res = d.resolution
        const donut = [
          { name: 'resolved YES', value: res.YES, color: C.profit },
          { name: 'resolved NO', value: res.NO, color: C.loss },
          { name: 'tie / 50-50', value: res.tie, color: C.warn },
          { name: 'unresolved', value: res.unresolved, color: C.muted },
        ].filter((s) => s.value > 0)
        const years = d.markets_by_year
        const cats = d.by_category
        const maxCat = Math.max(...cats.map((c) => c.n_markets), 1)
        return (
          <div className="space-y-4">
            {/* coverage tiles */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Markets (conditions)" value={d.coverage.total_conditions} format={(n) => int(n)} sub="on-chain conditions" />
              <Stat label="Resolved" value={d.coverage.resolved_conditions} format={(n) => int(n)}
                sub={`${((res.resolved / res.total) * 100).toFixed(1)}% of all markets`} tone="profit" />
              <Stat label="Fills indexed" value={d.coverage.total_fills} format={(n) => int(n)} sub="CLOB OrderFilled events" accent />
              <Stat label="Coverage" value={`${d.coverage.market_date_min ?? '—'} → ${d.coverage.market_date_max ?? '—'}`}
                sub={`HF head @ block ${int(d.coverage.cutoff_block)}`} />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              {/* resolution outcome donut */}
              <Panel>
                <div className="mb-2 label text-sig">resolution outcomes · on-chain payout argmax</div>
                <div className="h-[240px] w-full">
                  <ResponsiveContainer>
                    <PieChart>
                      <Pie data={donut} dataKey="value" nameKey="name" innerRadius={58} outerRadius={92} paddingAngle={2} stroke="none">
                        {donut.map((s) => <Cell key={s.name} fill={s.color} />)}
                      </Pie>
                      <Tooltip content={({ active, payload }: any) => active && payload?.length ? (
                        <div className="panel p-2 text-xs num"><span style={{ color: payload[0].payload.color }}>{payload[0].name}</span> · {int(payload[0].value)}</div>
                      ) : null} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-2xs">
                  {donut.map((s) => (
                    <span key={s.name} className="flex items-center gap-1.5 text-ink-2">
                      <span className="h-2 w-2 rounded-sm" style={{ background: s.color }} />{s.name} <span className="num text-muted">{int(s.value)}</span>
                    </span>
                  ))}
                </div>
              </Panel>

              {/* markets created per year */}
              <Panel>
                <div className="mb-2 label text-sig">markets created · by year</div>
                <div className="h-[240px] w-full" ref={barRef}>
                  <ResponsiveContainer>
                    <BarChart data={years} margin={{ left: 2, right: 8, top: 8, bottom: 4 }}>
                      <CartesianGrid stroke={C.line} vertical={false} />
                      <XAxis dataKey="year" stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} />
                      <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} width={44} tickFormatter={(v) => int(v)} />
                      <Tooltip cursor={{ fill: C.elevated }} content={({ active, payload, label }: any) => active && payload?.length ? (
                        <div className="panel p-2 text-xs num"><span className="text-muted">{label}</span> · <span className="text-sig">{int(payload[0].value)}</span> markets</div>
                      ) : null} />
                      <Bar dataKey="n" fill={C.sig} radius={[2, 2, 0, 0]} isAnimationActive={barIn} animationDuration={700} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Panel>
            </div>

            {/* category structure */}
            <Panel>
              <div className="mb-3 label text-sig">markets by category · resolved share</div>
              <div className="space-y-2">
                {cats.map((c) => (
                  <div key={c.category} className="flex items-center gap-3 text-xs">
                    <span className="w-28 shrink-0 capitalize text-ink-2">{c.category}</span>
                    <div className="relative h-4 flex-1 overflow-hidden rounded bg-bg">
                      <div className="absolute inset-y-0 left-0 rounded" style={{ width: `${(c.n_markets / maxCat) * 100}%`, background: `${C.sig}33` }} />
                      <div className="absolute inset-y-0 left-0 rounded" style={{ width: `${(c.n_resolved / maxCat) * 100}%`, background: C.sig }} />
                    </div>
                    <span className="num w-40 shrink-0 text-right text-muted">{int(c.n_resolved)} / {int(c.n_markets)} resolved</span>
                  </div>
                ))}
              </div>
            </Panel>

            <Caveat kind="note">
              Source: <a className="link-underline hover:text-sig" href={`https://huggingface.co/datasets/${d.coverage.repo}`} target="_blank" rel="noreferrer">{d.coverage.repo} ↗</a>.
              {' '}{d.note} The dataset is a snapshot to ~block {int(d.coverage.cutoff_block)}; anything newer comes from the live RPC dispute feed above.
            </Caveat>
          </div>
        )
      }}</Async>
    </Section>
  )
}
