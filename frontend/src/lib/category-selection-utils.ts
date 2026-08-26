import type { Category } from '../types'

export function resolveSelectedCategory(
  categories: Category[],
  value: string,
  currentCategory?: Category | null
): Category | undefined {
  return categories.find((category) => category.id === value)
    ?? (currentCategory?.id === value ? currentCategory : undefined)
}

export function isCategoryHiddenFromSelection(
  categories: Category[],
  category?: Category
): boolean {
  return Boolean(
    category
    && (category.is_hidden || !categories.some((item) => item.id === category.id))
  )
}
