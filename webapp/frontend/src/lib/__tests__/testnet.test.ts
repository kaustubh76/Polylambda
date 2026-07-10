import { describe, expect, it } from 'vitest'
import { AMOY, TEST_USDC, addressUrl, txUrl } from '../testnet'

describe('testnet constants + url helpers', () => {
  it('Amoy chain identity', () => {
    expect(AMOY.id).toBe(80002)
    expect(AMOY.hex).toBe('0x13882')
    expect(parseInt(AMOY.hex, 16)).toBe(AMOY.id)
  })
  it('explorer url builders', () => {
    expect(txUrl('0xabc')).toBe('https://amoy.polygonscan.com/tx/0xabc')
    expect(addressUrl('0xdef')).toBe('https://amoy.polygonscan.com/address/0xdef')
  })
  it('test USDC is 6-decimals', () => {
    expect(TEST_USDC.decimals).toBe(6)
    expect(TEST_USDC.address.startsWith('0x')).toBe(true)
  })
})
