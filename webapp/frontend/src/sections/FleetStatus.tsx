import { useState } from 'react'
import { api, usePoll, type TnAblation, type TnFleet, type TnKeeper } from '../api/client'
import { useToast } from '../components/Toast'
import { Caveat, ConfirmDialog, Panel, Pill, Section, Stat } from '../components/ui'
import { short } from '../lib/format'
import { addressUrl, txUrl } from '../lib/testnet'

const POLL_MS = 5000
const POL_LOW_WATER = 0.15

// The continuous testnet execution engine: the REAL production loop (estimators → quote → exit
// gate) signing on-chain transactions across the Amoy fleet, risk-governed. Nothing simulated —
// every number here is on-chain state or the keeper's own risk ledger.
export function FleetStatus() {
  const toast = useToast()
  const [fleet, setFleet] = useState<TnFleet | null>(null)
  const [keeper, setKeeper] = useState<TnKeeper | null>(null)
  const [abl, setAbl] = useState<TnAblation | null>(null)
  const [confirmKill, setConfirmKill] = useState(false)
  const [busy, setBusy] = useState(false)

  usePoll(async () => {
    const [f, k, a] = await Promise.allSettled([api.tnFleet(), api.tnKeeper(), api.tnAblation()])
    if (f.status === 'fulfilled') setFleet(f.value)
    if (k.status === 'fulfilled') setKeeper(k.value)
    if (a.status === 'fulfilled') setAbl(a.value)
    return f.status === 'fulfilled' && k.status === 'fulfilled'
  }, POLL_MS)

  const risk = keeper?.risk
  const killed = risk?.killed ?? false
  const engine = keeper?.engine
  const polLow = engine?.pol != null && engine.pol < POL_LOW_WATER

  const doKill = async () => {
    setBusy(true)
    try {
      await api.tnKill()
      toast.success('kill-switch engaged', { message: 'every signing path halts within one tick' })
    } catch (e: any) {
      toast.error('kill failed', { message: e?.message || 'request failed' })
    } finally {
      setBusy(false)
      setConfirmKill(false)
    }
  }
  const doUnkill = async () => {
    setBusy(true)
    try {
      await api.tnUnkill()
      toast.success('kill-switch cleared', { message: 'the keeper resumes signing' })
    } catch (e: any) {
      toast.error('unkill failed', { message: e?.message || 'request failed' })
    } finally {
      setBusy(false)
    }
  }
  const doBurst = async () => {
    try {
      const r = await api.tnKeeperRun(10)
      toast.success(r.started ? 'keeper burst started' : 'keeper already running',
        { message: r.started ? '10 ticks in the background' : 'continuous thread is alive' })
    } catch (e: any) {
      toast.error('burst failed', { message: e?.message || 'request failed' })
    }
  }

  const markets = fleet?.markets ?? []
  return (
    <Section id="fleet" kicker="testnet execution · continuous engine"
      title="Fleet & keeper"
      subtitle="The production quoting loop running LIVE against the Amoy fleet: engine-signed quotes, real on-chain fills, confirmed-dispute defense, and a risk governor in front of every transaction. No paper mode — nothing here is simulated."
      right={
        <div className="flex items-center gap-2">
          <Pill dot color={keeper?.running ? 'var(--ok)' : 'var(--warn)'}>
            {keeper?.running ? 'keeper running' : 'keeper idle'}
          </Pill>
          {killed
            ? <button className="btn !py-1 text-2xs" disabled={busy} onClick={doUnkill}>clear kill-switch</button>
            : <button className="btn !py-1 text-2xs border-warn/50 text-warn hover:bg-warn/10"
                onClick={() => setConfirmKill(true)}>KILL</button>}
        </div>
      }>
      <div className="space-y-4">
        {killed && (
          <Caveat kind="calibration">
            kill-switch engaged — the keeper keeps ticking but signs <b>zero</b> transactions until cleared
            {risk?.halt_reason ? ` (${risk.halt_reason})` : ''}.
          </Caveat>
        )}
        {keeper && !keeper.running && !killed && (
          <Caveat kind="calibration">
            keeper idle — {keeper.engine_ready === false
              ? <>the <span className="font-mono">ENGINE_PRIVATE_KEY</span> secret is missing on the host, so it can't sign.</>
              : keeper.autostart === false
                ? <>autostart is off (<span className="font-mono">KEEPER_AUTOSTART</span> not set); it runs only in scheduled bursts until enabled.</>
                : <>the free-tier host spun down; the 15-min watchdog restarts it (or POST <span className="font-mono">/api/testnet/keeper/run</span>).</>}
          </Caveat>
        )}
        {!killed && risk?.halted && (
          <Caveat kind="underpowered">signing halted by the risk governor: {risk.halt_reason}</Caveat>
        )}
        {polLow && (
          <Caveat kind="note">
            engine gas is low ({engine!.pol!.toFixed(3)} POL) — top up via the{' '}
            <a className="underline" href="https://faucet.polygon.technology/" target="_blank" rel="noreferrer">Amoy faucet</a>.
          </Caveat>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          <Panel>
            <div className="label mb-3">keeper</div>
            <div className="grid grid-cols-2 gap-3">
              <Stat label="ticks" value={String(keeper?.ticks_done ?? '—')} sub={`every ${keeper?.interval_s ?? 60}s`} />
              <Stat label="markets" value={String(keeper?.n_markets ?? '—')} sub="keeper-managed" />
              <Stat label="last tick" value={keeper?.last_tick_ts ? `${Math.max(0, Math.round(Date.now() / 1000 - keeper.last_tick_ts))}s ago` : '—'}
                sub={keeper?.last_error ? 'last error below' : 'healthy'} tone={keeper?.last_error ? 'warn' : undefined} />
              <Stat label="signed txs" value={String(keeper?.clob?.tx_count ?? risk?.tx_count ?? '—')} sub="this session" />
            </div>
            {keeper?.clob?.last_tx && (
              <div className="mt-3 text-2xs text-muted">
                last tx: {keeper.clob.last_tx.kind} ·{' '}
                <a className="text-sig underline" href={txUrl(keeper.clob.last_tx.tx)} target="_blank" rel="noreferrer">
                  {short(keeper.clob.last_tx.tx, 8, 6)} ↗
                </a>
              </div>
            )}
            {keeper?.last_error && <div className="mt-2 break-all text-2xs text-warn">{keeper.last_error}</div>}
            <div className="mt-3 flex gap-2">
              <button className="btn !py-1 text-2xs" onClick={doBurst}>▸ run 10-tick burst</button>
            </div>
          </Panel>

          <Panel>
            <div className="label mb-3">risk governor</div>
            <div className="grid grid-cols-2 gap-3">
              <Stat label="daily loss" value={risk ? `$${risk.daily_loss_usd.toFixed(2)}` : '—'}
                sub={risk ? `cap $${risk.limits.max_daily_loss_usd}` : ''} tone={risk && risk.daily_loss_usd > 0 ? 'warn' : undefined} />
              <Stat label="gas today" value={risk ? `${risk.gas_pol.toFixed(3)} POL` : '—'}
                sub={risk ? `cap ${risk.limits.max_gas_pol_per_day}` : ''} />
              <Stat label="txs today" value={risk ? String(risk.tx_count) : '—'}
                sub={risk ? `cap ${risk.limits.max_tx_per_day}` : ''} />
              <Stat label="gross exposure" value={risk ? risk.gross_exposure.toFixed(2) : '—'}
                sub={risk ? `cap ${risk.limits.portfolio_gross_cap}` : ''} />
            </div>
            <div className="mt-3 text-2xs text-muted">
              consecutive RPC errors: {risk?.consecutive_errors ?? '—'} / {risk?.limits.max_consecutive_errors ?? '—'}
              {keeper?.detector && <> · dispute feed: {keeper.detector.cached_disputes} confirmed, {keeper.detector.confirmations}-block guard</>}
            </div>
          </Panel>

          <Panel>
            <div className="label mb-3">engine wallet</div>
            {engine ? (
              <>
                <div className="text-2xs text-muted">
                  <a className="text-sig underline" href={addressUrl(engine.address)} target="_blank" rel="noreferrer">
                    {short(engine.address, 8, 6)} ↗
                  </a>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <Stat label="POL (gas)" value={engine.pol != null ? engine.pol.toFixed(4) : '—'}
                    tone={polLow ? 'warn' : undefined} sub={polLow ? 'LOW — faucet top-up' : 'funded'} />
                  <Stat label="test USDC" value={engine.usdc != null ? engine.usdc.toFixed(2) : '—'} sub="collateral reserve" />
                </div>
              </>
            ) : (
              <div className="py-4 text-center text-sm text-muted">
                engine offline — the keeper has not run in this app instance yet
              </div>
            )}
          </Panel>
        </div>

        {/* live engine P&L — the λ-on vs λ-off edge on real on-chain fills (the traction story) */}
        {abl?.available && abl.lambda_on && abl.lambda_off && (
          <Panel>
            <div className="mb-3 flex items-center justify-between">
              <div className="label">live engine P&amp;L · λ-on vs λ-off (on-chain)</div>
              {abl.underpowered && <Pill color="var(--warn)">directional only</Pill>}
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="λ-ON P&L" value={`${abl.lambda_on.pnl >= 0 ? '+' : ''}${abl.lambda_on.pnl.toFixed(4)}`}
                sub={`${abl.lambda_on.n_fills} fills · ${abl.lambda_on.n_exits} exits`} tone={abl.lambda_on.pnl >= 0 ? 'profit' : 'loss'} />
              <Stat label="λ-OFF P&L" value={`${abl.lambda_off.pnl >= 0 ? '+' : ''}${abl.lambda_off.pnl.toFixed(4)}`}
                sub={`${abl.lambda_off.n_fills} fills · ${abl.lambda_off.n_exits} exits`} tone={abl.lambda_off.pnl >= 0 ? 'profit' : 'loss'} />
              <Stat label="ON − OFF" value={`${(abl.delta_on_minus_off?.pnl ?? 0) >= 0 ? '+' : ''}${(abl.delta_on_minus_off?.pnl ?? 0).toFixed(4)}`}
                sub="USDC (equity mark)" tone={(abl.delta_on_minus_off?.pnl ?? 0) >= 0 ? 'profit' : 'loss'} />
              <Stat label="disputes" value={String(abl.n_disputes ?? 0)} sub="survived on-chain" />
            </div>
            {keeper?.markets && keeper.markets.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-2xs">
                  <thead>
                    <tr className="border-b border-line uppercase tracking-wide text-muted">
                      <th className="py-1.5 pr-3">market</th><th className="py-1.5 pr-3">arm</th>
                      <th className="py-1.5 pr-3">inventory</th><th className="py-1.5 pr-3">cash</th>
                      <th className="py-1.5 pr-3">equity</th><th className="py-1.5 pr-3">exits</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keeper.markets.map((m) => (
                      <tr key={m.token_id} className="border-b border-line/40">
                        <td className="py-1.5 pr-3">{m.category}</td>
                        <td className="py-1.5 pr-3 font-mono">{m.arm === 'lambda_on' ? 'λ-on' : 'λ-off'}</td>
                        <td className="py-1.5 pr-3 num">{m.inventory.toFixed(2)}</td>
                        <td className="py-1.5 pr-3 num">{m.cash.toFixed(4)}</td>
                        <td className="py-1.5 pr-3 num">{m.equity_mark.toFixed(4)}</td>
                        <td className="py-1.5 pr-3 num">{m.n_exits}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {abl.caveat && <div className="mt-3"><Caveat kind="underpowered">{abl.caveat}</Caveat></div>}
          </Panel>
        )}

        <Panel pad={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-line text-2xs uppercase tracking-wide text-muted">
                  <th className="px-4 py-2.5">market</th>
                  <th className="px-4 py-2.5">category</th>
                  <th className="px-4 py-2.5">bid / ask</th>
                  <th className="px-4 py-2.5">max trade</th>
                  <th className="px-4 py-2.5">escrow</th>
                  <th className="px-4 py-2.5">open YES</th>
                  <th className="px-4 py-2.5">λ / σ</th>
                  <th className="px-4 py-2.5">state</th>
                </tr>
              </thead>
              <tbody>
                {markets.length === 0 && (
                  <tr><td colSpan={8} className="px-4 py-6 text-center text-muted">
                    {fleet?.note || 'no fleet deployed yet — run scripts/deploy_fleet.py'}
                  </td></tr>
                )}
                {markets.map((m) => (
                  <tr key={m.address} className="border-b border-line/60">
                    <td className="px-4 py-2.5">
                      <a className="font-mono text-2xs text-sig underline" href={m.explorer} target="_blank" rel="noreferrer">
                        {short(m.address, 6, 4)} ↗
                      </a>
                      <div className="text-2xs text-muted">{m.label || 'fleet'}</div>
                    </td>
                    <td className="px-4 py-2.5">{m.category}</td>
                    <td className="px-4 py-2.5 font-mono text-2xs">
                      {m.deployed ? `${m.bid.toFixed(4)} / ${m.ask.toFixed(4)}` : '—'}
                    </td>
                    <td className="px-4 py-2.5">{m.max_trade ?? '—'}</td>
                    <td className="px-4 py-2.5">{m.escrow_usdc != null ? `$${m.escrow_usdc.toFixed(2)}` : '—'}</td>
                    <td className="px-4 py-2.5">{m.total_yes ?? '—'}</td>
                    <td className="px-4 py-2.5 font-mono text-2xs">
                      {m.lambda_jump != null ? `${m.lambda_jump.toFixed(4)} / ${(m.sigma ?? 0).toFixed(3)}` : '—'}
                    </td>
                    <td className="px-4 py-2.5">
                      {m.error ? <Pill dot color="var(--warn)">unreachable</Pill>
                        : m.resolved ? <Pill dot color="var(--muted)">resolved</Pill>
                        : m.disputed ? <Pill dot color="var(--warn)">disputed · buys halted</Pill>
                        : <Pill dot color="var(--ok)">quoting</Pill>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <ConfirmDialog open={confirmKill} onClose={() => setConfirmKill(false)} onConfirm={doKill}
        tone="warn" busy={busy} confirmLabel="Engage kill-switch"
        title="Engage the kill-switch?"
        body={<>Writes the cross-process kill file: <b>every</b> signing path (keeper, cron
          bursts) halts within one tick. The loop keeps running read-only; clear it here anytime.</>} />
    </Section>
  )
}
