import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { createPublicClient, createWalletClient, custom, formatUnits, http, parseUnits, type Address } from 'viem'
import { polygonAmoy } from 'viem/chains'
import { AMOY, AMOY_ADD_PARAMS, MARKET_ABI, TEST_USDC } from './testnet'

declare global {
  interface Window { ethereum?: any }
}

const ERC20 = [
  { type: 'function', name: 'balanceOf', stateMutability: 'view', inputs: [{ name: 'a', type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', name: 'allowance', stateMutability: 'view', inputs: [{ name: 'o', type: 'address' }, { name: 's', type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', name: 'approve', stateMutability: 'nonpayable', inputs: [{ name: 's', type: 'address' }, { name: 'v', type: 'uint256' }], outputs: [{ type: 'bool' }] },
] as const

// reads always go over the public Amoy RPC (works regardless of the wallet's current chain)
export const publicClient = createPublicClient({ chain: polygonAmoy, transport: http(AMOY.rpc) })

export const hasProvider = () => typeof window !== 'undefined' && !!window.ethereum

export interface Balances { pol: string; usdc: string }

export async function readBalances(addr: Address): Promise<Balances> {
  const [wei, usdc] = await Promise.all([
    publicClient.getBalance({ address: addr }),
    publicClient.readContract({ address: TEST_USDC.address, abi: ERC20, functionName: 'balanceOf', args: [addr] }) as Promise<bigint>,
  ])
  return { pol: formatUnits(wei, 18), usdc: formatUnits(usdc, TEST_USDC.decimals) }
}

export async function readAllowance(owner: Address, spender: Address): Promise<string> {
  const a = (await publicClient.readContract({ address: TEST_USDC.address, abi: ERC20, functionName: 'allowance', args: [owner, spender] })) as bigint
  return formatUnits(a, TEST_USDC.decimals)
}

const WAS_CONNECTED = 'pl:wallet-connected'
const BAL_POLL_MS = 12000

export interface WalletState {
  installed: boolean
  address: Address | null
  chainId: number | null
  onAmoy: boolean
  connecting: boolean
  error: string | null
  balances: Balances | null
  connect: () => Promise<void>
  disconnect: () => void
  ensureAmoy: () => Promise<void>
  approveToken: (spender: Address, amount: string) => Promise<`0x${string}`>
  buyYes: (market: Address, sizeYes: string) => Promise<`0x${string}`>
  sellYes: (market: Address, sizeYes: string) => Promise<`0x${string}`>
  redeem: (market: Address) => Promise<`0x${string}`>
  refreshBalances: () => Promise<void>
  clearError: () => void
}

const WalletContext = createContext<WalletState | null>(null)

// The one shared wallet instance — header + sections read the same state (no desync).
export function WalletProvider({ children }: { children: ReactNode }) {
  const value = useWalletState()
  return <WalletContext.Provider value={value}>{children}</WalletContext.Provider>
}

export function useWallet(): WalletState {
  const ctx = useContext(WalletContext)
  if (!ctx) throw new Error('useWallet must be used within <WalletProvider>')
  return ctx
}

function useWalletState(): WalletState {
  const [address, setAddress] = useState<Address | null>(null)
  const [chainId, setChainId] = useState<number | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [balances, setBalances] = useState<Balances | null>(null)

  const onAmoy = chainId === AMOY.id

  useEffect(() => {
    if (!hasProvider()) return
    const eth = window.ethereum
    // rehydrate an already-authorized wallet on reload
    eth.request({ method: 'eth_accounts' }).then((a: string[]) => { if (a?.[0]) setAddress(a[0] as Address) }).catch(() => {})
    eth.request({ method: 'eth_chainId' }).then((c: string) => setChainId(parseInt(c, 16))).catch(() => {})
    const onAcc = (a: string[]) => {
      const next = (a?.[0] as Address) ?? null
      setAddress(next)
      if (!next) { try { localStorage.removeItem(WAS_CONNECTED) } catch { /* ignore */ } }
    }
    const onChain = (c: string) => setChainId(parseInt(c, 16))
    eth.on?.('accountsChanged', onAcc)
    eth.on?.('chainChanged', onChain)
    return () => { eth.removeListener?.('accountsChanged', onAcc); eth.removeListener?.('chainChanged', onChain) }
  }, [])

  const refreshBalances = useCallback(async () => {
    if (!address || !onAmoy) { setBalances(null); return }
    try { setBalances(await readBalances(address)) } catch { /* transient */ }
  }, [address, onAmoy])

  // poll balances while connected on Amoy so external faucet funding shows up without a manual click
  useEffect(() => {
    if (!address || !onAmoy) { setBalances(null); return }
    refreshBalances()
    const t = setInterval(refreshBalances, BAL_POLL_MS)
    return () => clearInterval(t)
  }, [address, onAmoy, refreshBalances])

  const connect = useCallback(async () => {
    if (!hasProvider()) { setError('No EVM wallet found — install MetaMask to continue.'); return }
    setConnecting(true); setError(null)
    try {
      const a: string[] = await window.ethereum.request({ method: 'eth_requestAccounts' })
      setAddress((a?.[0] as Address) ?? null)
      const c: string = await window.ethereum.request({ method: 'eth_chainId' })
      setChainId(parseInt(c, 16))
      try { localStorage.setItem(WAS_CONNECTED, '1') } catch { /* ignore */ }
    } catch (e: any) {
      setError(e?.shortMessage || e?.message || 'connection rejected')
    } finally {
      setConnecting(false)
    }
  }, [])

  const disconnect = useCallback(() => {
    // EIP has no true disconnect — clear local state and best-effort revoke the permission
    setAddress(null); setBalances(null); setError(null)
    try { localStorage.removeItem(WAS_CONNECTED) } catch { /* ignore */ }
    window.ethereum?.request?.({ method: 'wallet_revokePermissions', params: [{ eth_accounts: {} }] }).catch(() => {})
  }, [])

  const ensureAmoy = useCallback(async () => {
    setError(null)
    try {
      await window.ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: AMOY.hex }] })
    } catch (e: any) {
      if (e?.code === 4902) {
        await window.ethereum.request({ method: 'wallet_addEthereumChain', params: [AMOY_ADD_PARAMS] })
      } else {
        setError(e?.shortMessage || e?.message || 'network switch rejected')
        throw e
      }
    }
  }, [])

  const walletClient = useCallback(() => {
    if (!address) throw new Error('connect a wallet first')
    return createWalletClient({ account: address, chain: polygonAmoy, transport: custom(window.ethereum) })
  }, [address])

  const approveToken = useCallback(async (spender: Address, amount: string): Promise<`0x${string}`> => {
    const hash = await walletClient().writeContract({ address: TEST_USDC.address, abi: ERC20,
      functionName: 'approve', args: [spender, parseUnits(amount, TEST_USDC.decimals)] })
    await publicClient.waitForTransactionReceipt({ hash })
    return hash
  }, [walletClient])

  const marketWrite = useCallback(async (
    market: Address, fn: 'buyYes' | 'sellYes' | 'redeem', args: readonly bigint[] = [],
  ): Promise<`0x${string}`> => {
    const hash = await walletClient().writeContract({ address: market, abi: MARKET_ABI, functionName: fn, args } as any)
    await publicClient.waitForTransactionReceipt({ hash })
    return hash
  }, [walletClient])

  const buyYes = useCallback((market: Address, sizeYes: string) => marketWrite(market, 'buyYes', [parseUnits(sizeYes, 6)]), [marketWrite])
  const sellYes = useCallback((market: Address, sizeYes: string) => marketWrite(market, 'sellYes', [parseUnits(sizeYes, 6)]), [marketWrite])
  const redeem = useCallback((market: Address) => marketWrite(market, 'redeem', []), [marketWrite])

  return {
    installed: hasProvider(), address, chainId, onAmoy,
    connecting, error, balances,
    connect, disconnect, ensureAmoy, approveToken, buyYes, sellYes, redeem, refreshBalances,
    clearError: () => setError(null),
  }
}
