export const PAGE_SIZE = 50

/** 1-based page from `?page=`; anything missing or invalid is page 1. */
export function parsePage(params: URLSearchParams): number {
  const n = Number(params.get("page"))
  return Number.isInteger(n) && n > 1 ? n : 1
}

export function pageCount(total: number, limit: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / limit))
}
