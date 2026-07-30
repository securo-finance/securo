import { format } from 'date-fns'

export function localDateString(date = new Date()) {
  return format(date, 'yyyy-MM-dd')
}
