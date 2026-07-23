// Polygon Amoy testnet constants + explorer link helpers for the read-only dashboard.
// Everything here is TESTNET — no real value. The engine wallet signs server-side; the dashboard
// only READS chain state and links out to the Amoy explorer.

export const AMOY = {
  id: 80002,
  hex: '0x13882',
  name: 'Polygon Amoy',
  rpc: 'https://rpc-amoy.polygon.technology',
  explorer: 'https://amoy.polygonscan.com',
  native: { name: 'POL', symbol: 'POL', decimals: 18 },
} as const

// Circle's testnet USDC on Polygon Amoy (verified on-chain: symbol "USDC", 6 decimals).
export const TEST_USDC = {
  address: '0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582' as `0x${string}`,
  symbol: 'USDC',
  decimals: 6,
} as const

export const txUrl = (hash: string) => `${AMOY.explorer}/tx/${hash}`
export const addressUrl = (addr: string) => `${AMOY.explorer}/address/${addr}`
