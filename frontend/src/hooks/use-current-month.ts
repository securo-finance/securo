import { useQuery } from '@tanstack/react-query'

import { months } from '@/lib/api'

export function useCurrentMonth() {
  return useQuery({
    queryKey: ['current-month'],
    queryFn: months.current,
    staleTime: 60_000,
  })
}
