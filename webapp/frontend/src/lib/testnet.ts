// Polygon Amoy testnet constants for the client-side wallet demo.
// Everything here is TESTNET — no real value. Signing happens in the user's wallet; no keys server-side.

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

export const FAUCETS = {
  pol: 'https://faucet.polygon.technology/', // testnet POL (gas)
  usdc: 'https://faucet.circle.com/',        // testnet USDC on Amoy
}

export const txUrl = (hash: string) => `${AMOY.explorer}/tx/${hash}`
export const addressUrl = (addr: string) => `${AMOY.explorer}/address/${addr}`

// Minimal ABI for the user-signed calls against PolyLambdaMarket (buy/sell/redeem + a couple views).
// The engine-only fns (postQuote/flagDispute/resolve) are signed by the backend, not here.
export const MARKET_ABI = [
  { type: 'function', name: 'buyYes', stateMutability: 'nonpayable', inputs: [{ name: 'size', type: 'uint256' }], outputs: [] },
  { type: 'function', name: 'sellYes', stateMutability: 'nonpayable', inputs: [{ name: 'size', type: 'uint256' }], outputs: [] },
  { type: 'function', name: 'redeem', stateMutability: 'nonpayable', inputs: [], outputs: [] },
  { type: 'function', name: 'yesShares', stateMutability: 'view', inputs: [{ name: 'a', type: 'address' }], outputs: [{ type: 'uint256' }] },
] as const

// EIP-3085 params for wallet_addEthereumChain (if the wallet doesn't know Amoy yet).
export const AMOY_ADD_PARAMS = {
  chainId: AMOY.hex,
  chainName: AMOY.name,
  nativeCurrency: AMOY.native,
  rpcUrls: [AMOY.rpc],
  blockExplorerUrls: [AMOY.explorer],
}
