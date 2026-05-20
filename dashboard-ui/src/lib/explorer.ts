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
