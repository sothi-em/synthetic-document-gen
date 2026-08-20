import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Truncate a long string by cutting out the middle and replacing it with
 * "...". Short strings are returned unchanged.
 *
 * @param value - The string to truncate.
 * @param maxLength - Maximum length of the result (default 32).
 * @returns The original string, or a middle-truncated version.
 */
export function truncateMiddle(value: string, maxLength = 32): string {
  if (value.length <= maxLength) return value
  const ellipsis = "..."
  const keep = maxLength - ellipsis.length
  const head = Math.ceil(keep / 2)
  const tail = Math.floor(keep / 2)
  return `${value.slice(0, head)}${ellipsis}${value.slice(value.length - tail)}`
}
