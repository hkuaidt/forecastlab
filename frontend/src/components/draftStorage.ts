/** Migrate the saved identifier without restoring or confirming a draft. */
export const DRAFT_STORAGE_KEY = 'forecastlab.question-framing.draft_id'
const LEGACY_DRAFT_STORAGE_KEY = 'forecastlab.agent12.draft_id'

export function migrateDraftStorage() {
  try {
    const previous = localStorage.getItem(LEGACY_DRAFT_STORAGE_KEY)
    if (previous && !localStorage.getItem(DRAFT_STORAGE_KEY)) {
      localStorage.setItem(DRAFT_STORAGE_KEY, previous)
    }
    localStorage.removeItem(LEGACY_DRAFT_STORAGE_KEY)
  } catch { /* Keep the previous key if storage cannot be written. */ }
}
