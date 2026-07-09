import { useCallback, useEffect, useState } from 'react'
import type { Address } from 'viem'
import { readAllowance, readBalances, useWallet, type Balances } from '../lib/wallet'
import { AMOY, DEMO_SPENDER, DEMO_SPENDER_LABEL, FAUCETS, addressUrl, txUrl } from '../lib/testnet'
import { C } from '../lib/theme'
import { num, short } from '../lib/format'
import { Caveat, ErrorBox, Panel, Section, Stat } from '../components/ui'

type Tx = { state: 'idle' | 'pending' | 'ok' | 'err'; hash?: string; msg?: string }

export function TestnetWallet() {
  const w = useWallet()
  const [bal, setBal] = useState<Balances | null>(null)
  const [allowance, setAllowance] = useState<string | null>(null)
  const [amount, setAmount] = useState('100')
  const [tx, setTx] = useState<Tx>({ state: 'idle' })

  const refresh = useCallback(async () => {
    if (!w.address || !w.onAmoy) return
    try {
      const [b, a] = await Promise.all([readBalances(w.address), readAllowance(w.address, DEMO_SPENDER)])
      setBal(b); setAllowance(a)
    } catch { /* transient RPC hiccup — keep last values */ }
  }, [w.address, w.onAmoy])

  useEffect(() => { refresh() }, [refresh])

  const grant = async () => {
    if (!w.address) return
    setTx({ state: 'pending' })
    try {
      const hash = await w.approveUsdc(DEMO_SPENDER as Address, amount || '0')
      setTx({ state: 'ok', hash })
      refresh()
    } catch (e: any) {
      setTx({ state: 'err', msg: e?.shortMessage || e?.message || 'transaction failed' })
    }
  }

  return (
    <Section id="wallet" kicker="live on-chain · Polygon Amoy testnet"
      title="Testnet wallet — the real pre-trade approve"
      subtitle="Connect a wallet, switch to Polygon Amoy, and run the exact on-chain step the live engine needs before it can quote: an ERC-20 allowance to the exchange. Real transaction, testnet assets — no mainnet risk, no keys on the server."
      right={
        <span className={`chip ${w.onAmoy ? 'border-sig/40 text-sig' : w.address ? 'border-warn/50 text-warn' : ''}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${w.onAmoy ? 'animate-pulse2' : ''}`}
            style={{ background: w.onAmoy ? C.sig : w.address ? C.warn : C.muted }} />
          {w.onAmoy ? 'on Amoy' : w.address ? 'wrong network' : 'not connected'}
        </span>
      }>

      {/* --- not installed --- */}
      {!w.installed && (
        <Panel>
          <div className="text-sm text-ink-2">No EVM wallet detected in this browser.</div>
          <a href="https://metamask.io/download/" target="_blank" rel="noreferrer" className="btn btn-primary mt-3">Install MetaMask</a>
          <div className="mt-3"><Caveat kind="note">This whole flow is Polygon <b>Amoy testnet</b> — play money. Signing happens in your wallet; the app and server never hold a private key.</Caveat></div>
        </Panel>
      )}

      {/* --- installed, not connected --- */}
      {w.installed && !w.address && (
        <Panel className="flex flex-col items-start gap-3">
          <div className="text-sm text-ink-2">Connect a wallet to read your testnet balances and sign the on-chain approve.</div>
          <button className="btn btn-primary" onClick={w.connect} disabled={w.connecting}>
            {w.connecting ? 'connecting…' : '🦊 Connect wallet'}
          </button>
          {w.error && <ErrorBox error={w.error} />}
          <Caveat kind="note">Polygon <b>Amoy testnet</b> only — no real value. No keys touch the server; you sign every action.</Caveat>
        </Panel>
      )}

      {/* --- connected, wrong network --- */}
      {w.address && !w.onAmoy && (
        <Panel className="flex flex-wrap items-center gap-3">
          <span className="chip"><span className="h-1.5 w-1.5 rounded-full bg-sig" />{short(w.address, 6, 4)}</span>
          <span className="text-sm text-ink-2">Wallet is on chain {w.chainId ?? '—'} — switch to {AMOY.name} (chain {AMOY.id}) to continue.</span>
          <button className="btn btn-primary ml-auto" onClick={w.ensureAmoy}>Switch to Polygon Amoy</button>
          {w.error && <div className="w-full"><ErrorBox error={w.error} /></div>}
        </Panel>
      )}

      {/* --- connected on Amoy: the live surface --- */}
      {w.address && w.onAmoy && (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          {/* balances rail */}
          <div className="space-y-3 self-start">
            <div className="flex items-center justify-between">
              <a href={addressUrl(w.address)} target="_blank" rel="noreferrer" className="chip link-underline">
                <span className="h-1.5 w-1.5 rounded-full bg-sig" />{short(w.address, 6, 4)}
              </a>
              <button className="text-2xs text-muted hover:text-ink-2" onClick={refresh}>↻ refresh</button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Stat label="POL (gas)" value={bal ? num(+bal.pol, 3) : '—'} accent sub="native testnet token" />
              <Stat label="test USDC" value={bal ? num(+bal.usdc, 2) : '—'} accent sub="Circle Amoy faucet" />
            </div>
            <div className="flex gap-2">
              <a href={FAUCETS.pol} target="_blank" rel="noreferrer" className="btn flex-1 !py-1.5 text-2xs">POL faucet ↗</a>
              <a href={FAUCETS.usdc} target="_blank" rel="noreferrer" className="btn flex-1 !py-1.5 text-2xs">USDC faucet ↗</a>
            </div>
          </div>

          {/* action */}
          <Panel>
            <div className="label mb-2 text-sig">grant allowance · ERC-20 approve</div>
            <p className="mb-3 text-sm leading-relaxed text-ink-2">
              Approve <span className="num text-ink">{DEMO_SPENDER_LABEL}</span> to spend your test USDC — the same on-chain
              authorization the mainnet engine sends before it can post quotes. Only POL (gas) is needed; approve doesn't move USDC.
            </p>
            <div className="mb-3 flex items-end gap-3">
              <label className="flex-1">
                <div className="label mb-1">allowance amount (USDC)</div>
                <input className="field num" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ''))} inputMode="decimal" />
              </label>
              <button className="btn btn-primary" onClick={grant} disabled={tx.state === 'pending'}>
                {tx.state === 'pending' ? 'confirm in wallet…' : 'Grant allowance'}
              </button>
            </div>

            {/* tx status */}
            {tx.state === 'ok' && tx.hash && (
              <div className="rounded-lg border border-good/30 bg-good/10 p-3 text-sm">
                <span className="text-good">✓ approved on-chain.</span>{' '}
                <a href={txUrl(tx.hash)} target="_blank" rel="noreferrer" className="num link-underline text-ink-2">{short(tx.hash, 10, 8)} ↗</a>
              </div>
            )}
            {tx.state === 'err' && <ErrorBox error={tx.msg || 'transaction failed'} />}

            <div className="num mt-3 flex items-center justify-between border-t border-line pt-3 text-2xs text-muted">
              <span>on-chain allowance → {DEMO_SPENDER_LABEL}</span>
              <span className={allowance && +allowance > 0 ? 'text-sig' : ''}>{allowance != null ? `${num(+allowance, 2)} USDC` : '—'}</span>
            </div>
          </Panel>
        </div>
      )}

      <div className="mt-4">
        <Caveat kind="note">
          <b>Testnet demo.</b> Polygon Amoy assets have no value; the spender is the real
          {' '}<a href={addressUrl(DEMO_SPENDER)} target="_blank" rel="noreferrer" className="num link-underline">CTF Exchange address</a> so the
          step mirrors production 1:1. Polymarket's CLOB itself is mainnet-only — live orders stay jurisdiction-gated and out of scope for this MVP.
        </Caveat>
      </div>
    </Section>
  )
}
