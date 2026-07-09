import { useCallback, useEffect, useState } from 'react'
import { createPublicClient, createWalletClient, custom, formatUnits, http, parseUnits, type Address } from 'viem'
import { polygonAmoy } from 'viem/chains'
import { AMOY, AMOY_ADD_PARAMS, TEST_USDC } from './testnet'

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

export function useWallet() {
  const [address, setAddress] = useState<Address | null>(null)
  const [chainId, setChainId] = useState<number | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hasProvider()) return
    const eth = window.ethereum
    eth.request({ method: 'eth_accounts' }).then((a: string[]) => { if (a?.[0]) setAddress(a[0] as Address) }).catch(() => {})
    eth.request({ method: 'eth_chainId' }).then((c: string) => setChainId(parseInt(c, 16))).catch(() => {})
    const onAcc = (a: string[]) => setAddress((a?.[0] as Address) ?? null)
    const onChain = (c: string) => setChainId(parseInt(c, 16))
    eth.on?.('accountsChanged', onAcc)
    eth.on?.('chainChanged', onChain)
    return () => { eth.removeListener?.('accountsChanged', onAcc); eth.removeListener?.('chainChanged', onChain) }
  }, [])

  const connect = useCallback(async () => {
    if (!hasProvider()) { setError('No EVM wallet found — install MetaMask to continue.'); return }
    setConnecting(true); setError(null)
    try {
      const a: string[] = await window.ethereum.request({ method: 'eth_requestAccounts' })
      setAddress((a?.[0] as Address) ?? null)
      const c: string = await window.ethereum.request({ method: 'eth_chainId' })
      setChainId(parseInt(c, 16))
    } catch (e: any) {
      setError(e?.shortMessage || e?.message || 'connection rejected')
    } finally {
      setConnecting(false)
    }
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

  const approveUsdc = useCallback(async (spender: Address, amount: string): Promise<`0x${string}`> => {
    if (!address) throw new Error('connect a wallet first')
    const wallet = createWalletClient({ account: address, chain: polygonAmoy, transport: custom(window.ethereum) })
    const value = parseUnits(amount, TEST_USDC.decimals)
    const hash = await wallet.writeContract({ address: TEST_USDC.address, abi: ERC20, functionName: 'approve', args: [spender, value] })
    await publicClient.waitForTransactionReceipt({ hash })
    return hash
  }, [address])

  return {
    installed: hasProvider(), address, chainId, onAmoy: chainId === AMOY.id,
    connecting, error, connect, ensureAmoy, approveUsdc, clearError: () => setError(null),
  }
}
