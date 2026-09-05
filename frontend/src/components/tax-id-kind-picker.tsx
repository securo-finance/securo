import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, ChevronsUpDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { countryFlag } from '@/lib/country-flag'
import { countryName } from '@/lib/country-name'
import { taxIdCountry, taxIdFlag } from '@/lib/tax-id-country'
import type { TaxIdKindOption } from '@/types'

/** Accent-insensitive, so "italia" finds "Itália" and "franca" finds "França". */
function normalise(value: string): string {
  return value
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
}

interface Props {
  /** Every document kind, as served by /api/fiscal/tax-id-kinds. */
  kinds: TaxIdKindOption[]
  /** Country to documents, so the list can be grouped and searched by country. */
  jurisdictions: { code: string; kinds: string[] }[]
  /** The active workspace's country, listed first. Null when unset. */
  activeJurisdiction: string | null
  value: string
  /**
   * The document number this row holds. Only used to place a `vat` id, whose
   * country lives inside the number rather than in the kind.
   */
  documentValue: string
  /** Kinds already used on this payee; shown but not selectable. */
  used: Set<string>
  onChange: (kind: string) => void
}

/**
 * Picks which fiscal document a row holds.
 *
 * Grouped by country and searchable, because the flat list it replaced asked
 * the user to already know that Partita IVA is Italian and SIRET is French.
 * Typing "Itália" now surfaces that country's documents, and typing "CNPJ"
 * finds it directly.
 *
 * The grouping is not hardcoded here: which documents a country uses comes
 * from the jurisdiction packs on the server. A copy in the browser would be a
 * second source of truth for the same fact.
 */
export function TaxIdKindPicker({
  kinds,
  jurisdictions,
  activeJurisdiction,
  value,
  documentValue,
  used,
  onChange,
}: Props) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  const locale = i18n.language

  const byKind = useMemo(
    () => new Map(kinds.map((k) => [k.kind, k])),
    [kinds],
  )
  const label = (kind: string) => {
    const option = byKind.get(kind)
    return option ? t(option.label_key, kind.toUpperCase()) : kind.toUpperCase()
  }
  // From the platform's own locale data rather than the translation files:
  // forty-odd country names across ten locales would be pure restatement, and
  // every new pack would add ten more strings to keep in sync.
  const country = (code: string) => countryName(code, locale)
  const selectedCountry = taxIdCountry(value, documentValue, jurisdictions)
  const selectedFlag = taxIdFlag(value, documentValue, jurisdictions)
  /**
   * Where the tick goes. A kind is listed under every country that asks for
   * it, so a Greek VAT id used to show as selected under Germany too, which
   * reads as though Germany were the choice. When the value tells us the
   * country, only that country is ticked; while the field is still empty
   * nothing has narrowed it down, so every instance is.
   */
  const isSelected = (kind: string, jurisdiction: string) =>
    kind === value && (!selectedCountry || selectedCountry === jurisdiction)
  // JSX rather than a string so the flag can be given room: the group's own
  // padding puts it flush against the edge otherwise.
  const groupHeading = (label: string, flag?: string) => (
    <span className="flex items-center gap-2 pl-1.5">
      {flag ? <span aria-hidden>{flag}</span> : null}
      <span>{label}</span>
    </span>
  )
  // Scoped to this picker rather than to CommandGroup itself: the global search
  // palette uses the same primitive and its headings are spaced as they are on
  // purpose. Without this the country name sits flush against the first
  // document under it, so the two read as one line.
  const groupClass = '[&_[cmdk-group-heading]]:pb-2 [&_[cmdk-group-heading]]:pt-1'

  // The workspace's own country first: it is what the overwhelming majority of
  // rows will use, and scrolling past seven other countries to reach it would
  // be the same mistake in a different shape.
  const groups = useMemo(() => {
    const ordered = [...jurisdictions].sort((a, b) => {
      if (a.code === activeJurisdiction) return -1
      if (b.code === activeJurisdiction) return 1
      // Locale-aware, so the accented names sort where a reader expects them
      // rather than after Z.
      return country(a.code).localeCompare(country(b.code), locale)
    })
    return ordered.filter((j) => j.kinds.length > 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jurisdictions, activeJurisdiction, locale])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-48 shrink-0 justify-between font-normal"
          // The longest labels ("Número de IVA") still truncate once the flag
          // takes its share of the width, so the full one stays reachable.
          title={label(value)}
        >
          {/* The flag stays once the list is closed and the country heading is
              gone with it. It earns its place on `vat`, whose label nineteen
              countries share: "VAT number" alone does not say whose. */}
          <span className="flex min-w-0 items-center gap-1.5">
            {selectedFlag ? (
              <span aria-hidden className="shrink-0">
                {selectedFlag}
              </span>
            ) : null}
            <span className="truncate">{label(value)}</span>
          </span>
          <ChevronsUpDown size={13} className="ml-1 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="start">
        {/* Substring rather than cmdk's default fuzzy scoring: these are
            proper nouns, and fuzzy matching surfaced "Inscrição estadual" for
            a search of "siret" (s-i-r-e-t as a subsequence), which is noise
            exactly where the user is trying to be precise. */}
        <Command
          filter={(value, search, keywords) => {
            const query = normalise(search)
            if (!query) return 1
            const haystack = normalise([value, ...(keywords ?? [])].join(' '))
            return haystack.includes(query) ? 1 : 0
          }}
        >
          {/* Searches document names and country names alike: `keywords`
              carries the country so "Itália" matches Partita IVA. */}
          <CommandInput placeholder={t('payees.searchTaxIdKind', 'Search document or country…')} />
          <CommandList className="max-h-[280px]">
            <CommandEmpty>{t('common.noResults', 'No results')}</CommandEmpty>
            {groups.map((jurisdiction) => (
              <CommandGroup
                key={jurisdiction.code}
                className={groupClass}
                heading={groupHeading(country(jurisdiction.code), countryFlag(jurisdiction.code))}
              >
                {jurisdiction.kinds.map((kind) => (
                  <CommandItem
                    key={`${jurisdiction.code}-${kind}`}
                    value={`${jurisdiction.code}-${kind}`}
                    keywords={[label(kind), country(jurisdiction.code)]}
                    disabled={kind !== value && used.has(kind)}
                    onSelect={() => {
                      onChange(kind)
                      setOpen(false)
                    }}
                  >
                    <span className="flex-1">{label(kind)}</span>
                    {isSelected(kind, jurisdiction.code) && (
                      <Check size={13} className="text-primary" />
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
            {/* Once, at the end: the escape hatch for a document no pack
                describes, rather than repeated under every country. */}
            {/* Same left padding as the country groups, so the headings line up
                whether or not they carry a flag. */}
            <CommandGroup
              className={groupClass}
              heading={groupHeading(t('fiscal.anyCountry', 'Anywhere else'))}
            >
              <CommandItem
                value="other"
                keywords={[label('other')]}
                disabled={value !== 'other' && used.has('other')}
                onSelect={() => {
                  onChange('other')
                  setOpen(false)
                }}
              >
                <span className="flex-1">{label('other')}</span>
                {value === 'other' && <Check size={13} className="text-primary" />}
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
