/**
 * Best-effort tx-hash → block-explorer URL.
 *
 * Detects the chain from the tx-hash format:
 *   - Starts with `0x` + 64 hex chars  → EVM (Ethereum / Base / Polygon / Arbitrum)
 *   - 87–88 char base58                → Solana
 *
 * For EVM, we default to etherscan but allow callers to override per-chain.
 * Returns null when the hash format is unrecognized (so the UI can fall
 * back to rendering the short hash without a link).
 */

export type Chain =
  | "solana"
  | "ethereum"
  | "base"
  | "bsc"
  | "polygon"
  | "arbitrum"
  | "optimism"
  | "unknown"

const EVM_EXPLORERS: Record<string, string> = {
  ethereum: "https://etherscan.io/tx/",
  base: "https://basescan.org/tx/",
  bsc: "https://bscscan.com/tx/",
  polygon: "https://polygonscan.com/tx/",
  arbitrum: "https://arbiscan.io/tx/",
  optimism: "https://optimistic.etherscan.io/tx/",
}

const SOLANA_EXPLORER = "https://solscan.io/tx/"

export function detectChainFromTxHash(hash: string | null | undefined): Chain {
  if (!hash) return "unknown"
  const h = hash.trim()
  if (/^0x[a-fA-F0-9]{64}$/.test(h)) return "ethereum"
  // Solana base58 transaction signatures are typically 87-88 chars in
  // base58 (32-byte signature). Heuristic: 85-90 char string with no
  // 0x prefix that fits the base58 alphabet.
  if (/^[1-9A-HJ-NP-Za-km-z]{85,90}$/.test(h)) return "solana"
  return "unknown"
}

export function detectChainFromAddress(addr: string | null | undefined): Chain {
  if (!addr) return "unknown"
  const a = addr.trim()
  // EVM: 0x + 40 hex chars
  if (/^0x[a-fA-F0-9]{40}$/.test(a)) return "ethereum"
  // Solana addresses are base58, typically 32-44 chars
  if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a)) return "solana"
  return "unknown"
}

const SOLANA_ADDR_EXPLORER = "https://solscan.io/account/"
const EVM_ADDR_EXPLORERS: Record<string, string> = {
  ethereum: "https://etherscan.io/address/",
  base: "https://basescan.org/address/",
  bsc: "https://bscscan.com/address/",
  polygon: "https://polygonscan.com/address/",
  arbitrum: "https://arbiscan.io/address/",
  optimism: "https://optimistic.etherscan.io/address/",
}

export function addressExplorerUrl(
  addr: string | null | undefined,
  chain?: Chain,
): string | null {
  if (!addr) return null
  const c = chain && chain !== "unknown" ? chain : detectChainFromAddress(addr)
  if (c === "solana") return SOLANA_ADDR_EXPLORER + addr
  const evmBase = EVM_ADDR_EXPLORERS[c]
  if (evmBase) return evmBase + addr
  return null
}

export function txExplorerUrl(
  hash: string | null | undefined,
  chain?: Chain,
): string | null {
  if (!hash) return null
  const c = chain && chain !== "unknown" ? chain : detectChainFromTxHash(hash)
  if (c === "solana") return SOLANA_EXPLORER + hash
  const evmBase = EVM_EXPLORERS[c]
  if (evmBase) return evmBase + hash
  return null
}
