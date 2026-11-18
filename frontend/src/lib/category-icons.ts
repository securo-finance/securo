import type { LucideIcon } from 'lucide-react'
import type { TFunction } from 'i18next'
import {
  House, UtensilsCrossed, Car, ShoppingCart, Pill, Gamepad2,
  Smartphone, BookOpen, ArrowLeftRight, CircleHelp,
  Wallet, CreditCard, Banknote, PiggyBank, TrendingUp, TrendingDown,
  Receipt, ShoppingBag, Gift, Heart, Baby, Dog, Cat,
  Plane, Train, Bus, Fuel, Bike,
  Lightbulb, Droplets, Flame, Wifi, Tv,
  GraduationCap, Briefcase, Building2, Landmark,
  Dumbbell, Shirt, Scissors, Wrench, Hammer,
  Music, Film, Coffee, Beer, Pizza, Salad,
  Stethoscope, Syringe, Cross,
  PartyPopper, TreePine, Umbrella, Globe, Sparkles,
  HeartHandshake, Brain, HandCoins, Ticket, Sofa, SprayCan, Package,
  BedDouble, Luggage, School, Trophy, Camera, Newspaper, Laptop, Monitor,
  Trees, Croissant, Cake, IceCreamCone, Apple, Beef, Fish,
  Motorbike, CarTaxiFront, PencilRuler, Refrigerator, PawPrint
} from 'lucide-react'

export interface CategoryIconEntry {
  name: string      // Lucide icon name (stored in DB)
  pt: string        // Portuguese label, used as fallback when a locale has no translation
  icon: LucideIcon  // Component reference
}

export const CATEGORY_ICONS: CategoryIconEntry[] = [
  // Moradia & Casa
  { name: 'house', pt: 'Casa / Moradia', icon: House },
  { name: 'lightbulb', pt: 'Luz / Energia', icon: Lightbulb },
  { name: 'droplets', pt: 'Água', icon: Droplets },
  { name: 'flame', pt: 'Gás', icon: Flame },
  { name: 'wifi', pt: 'Internet / Wi-Fi', icon: Wifi },
  { name: 'tv', pt: 'TV / Streaming', icon: Tv },
  { name: 'refrigerator', pt: 'Eletrodomésticos', icon: Refrigerator },
  { name: 'sofa', pt: 'Móveis / Decoração', icon: Sofa },
  { name: 'spray-can', pt: 'Limpeza / Diarista', icon: SprayCan },
  // Alimentação
  { name: 'utensils-crossed', pt: 'Alimentação / Restaurante', icon: UtensilsCrossed },
  { name: 'coffee', pt: 'Café', icon: Coffee },
  { name: 'beer', pt: 'Bebidas / Bar', icon: Beer },
  { name: 'pizza', pt: 'Pizza / Fast Food', icon: Pizza },
  { name: 'salad', pt: 'Salada / Saudável', icon: Salad },
  { name: 'croissant', pt: 'Padaria / Pão', icon: Croissant },
  { name: 'cake', pt: 'Doces / Confeitaria', icon: Cake },
  { name: 'ice-cream-cone', pt: 'Sorvete / Sobremesa', icon: IceCreamCone },
  { name: 'apple', pt: 'Feira / Hortifruti', icon: Apple },
  { name: 'beef', pt: 'Açougue / Carnes', icon: Beef },
  { name: 'fish', pt: 'Peixaria / Frutos do mar', icon: Fish },
  // Transporte
  { name: 'car', pt: 'Carro / Transporte', icon: Car },
  { name: 'fuel', pt: 'Combustível / Gasolina', icon: Fuel },
  { name: 'bus', pt: 'Ônibus', icon: Bus },
  { name: 'train', pt: 'Trem / Metrô', icon: Train },
  { name: 'plane', pt: 'Avião / Viagem', icon: Plane },
  { name: 'bike', pt: 'Bicicleta', icon: Bike },
  { name: 'motorbike', pt: 'Moto', icon: Motorbike },
  { name: 'car-taxi-front', pt: 'Táxi / App de corrida', icon: CarTaxiFront },
  { name: 'bed-double', pt: 'Hospedagem / Hotel', icon: BedDouble },
  { name: 'luggage', pt: 'Bagagem / Mala', icon: Luggage },
  // Compras
  { name: 'shopping-cart', pt: 'Mercado / Supermercado', icon: ShoppingCart },
  { name: 'shopping-bag', pt: 'Compras / Loja', icon: ShoppingBag },
  { name: 'gift', pt: 'Presente', icon: Gift },
  { name: 'package', pt: 'Compra online / Entrega', icon: Package },
  { name: 'shirt', pt: 'Roupa / Vestuário', icon: Shirt },
  // Saúde
  { name: 'pill', pt: 'Remédio / Farmácia', icon: Pill },
  { name: 'stethoscope', pt: 'Médico / Consulta', icon: Stethoscope },
  { name: 'syringe', pt: 'Vacina / Exame', icon: Syringe },
  { name: 'cross', pt: 'Hospital / Emergência', icon: Cross },
  { name: 'heart', pt: 'Saúde / Bem-estar', icon: Heart },
  { name: 'brain', pt: 'Saúde mental / Terapia', icon: Brain },
  { name: 'dumbbell', pt: 'Academia / Exercício', icon: Dumbbell },
  // Lazer
  { name: 'gamepad-2', pt: 'Jogos / Lazer', icon: Gamepad2 },
  { name: 'music', pt: 'Música', icon: Music },
  { name: 'film', pt: 'Cinema / Filme', icon: Film },
  { name: 'party-popper', pt: 'Festa / Evento', icon: PartyPopper },
  { name: 'ticket', pt: 'Ingresso / Eventos', icon: Ticket },
  { name: 'trophy', pt: 'Esporte / Competição', icon: Trophy },
  { name: 'camera', pt: 'Fotografia / Câmera', icon: Camera },
  { name: 'newspaper', pt: 'Jornal / Revista', icon: Newspaper },
  // Tecnologia & Assinaturas
  { name: 'smartphone', pt: 'Celular / Telefone', icon: Smartphone },
  { name: 'credit-card', pt: 'Cartão de Crédito', icon: CreditCard },
  { name: 'laptop', pt: 'Notebook / Tablet', icon: Laptop },
  { name: 'monitor', pt: 'Computador / Periféricos', icon: Monitor },
  // Educação
  { name: 'book-open', pt: 'Livro / Leitura', icon: BookOpen },
  { name: 'graduation-cap', pt: 'Educação / Curso', icon: GraduationCap },
  { name: 'school', pt: 'Escola / Mensalidade escolar', icon: School },
  { name: 'pencil-ruler', pt: 'Papelaria / Material escolar', icon: PencilRuler },
  // Trabalho & Negócios
  { name: 'briefcase', pt: 'Trabalho / Negócios', icon: Briefcase },
  { name: 'building-2', pt: 'Empresa / Escritório', icon: Building2 },
  { name: 'landmark', pt: 'Banco / Governo', icon: Landmark },
  // Finanças
  { name: 'wallet', pt: 'Carteira', icon: Wallet },
  { name: 'banknote', pt: 'Dinheiro', icon: Banknote },
  { name: 'piggy-bank', pt: 'Poupança / Investimento', icon: PiggyBank },
  { name: 'trending-up', pt: 'Rendimento / Lucro', icon: TrendingUp },
  { name: 'trending-down', pt: 'Prejuízo / Perda', icon: TrendingDown },
  { name: 'hand-coins', pt: 'Empréstimo / Dívida', icon: HandCoins },
  { name: 'receipt', pt: 'Recibo / Nota Fiscal', icon: Receipt },
  { name: 'arrow-left-right', pt: 'Transferência', icon: ArrowLeftRight },
  { name: 'heart-handshake', pt: 'Doação / Solidariedade', icon: HeartHandshake },
  // Família & Pets
  { name: 'baby', pt: 'Bebê / Criança', icon: Baby },
  { name: 'dog', pt: 'Pet / Cachorro', icon: Dog },
  { name: 'cat', pt: 'Pet / Gato', icon: Cat },
  { name: 'paw-print', pt: 'Pet / Geral', icon: PawPrint },
  // Serviços & Manutenção
  { name: 'scissors', pt: 'Cabelo / Beleza', icon: Scissors },
  { name: 'wrench', pt: 'Manutenção / Reparo', icon: Wrench },
  { name: 'hammer', pt: 'Construção / Reforma', icon: Hammer },
  // Outros
  { name: 'tree-pine', pt: 'Natureza / Jardim', icon: TreePine },
  { name: 'trees', pt: 'Floresta / Natureza', icon: Trees },
  { name: 'umbrella', pt: 'Seguro', icon: Umbrella },
  { name: 'globe', pt: 'Internacional', icon: Globe },
  { name: 'sparkles', pt: 'Especial', icon: Sparkles },
  { name: 'circle-help', pt: 'Outros / Indefinido', icon: CircleHelp },
]

// O(1) lookup map: icon-name → LucideIcon component
export const ICON_MAP: Record<string, LucideIcon> = Object.fromEntries(
  CATEGORY_ICONS.map((entry) => [entry.name, entry.icon])
)

// Icon labels follow the active UI language: tooltips and the picker search
// read the translation. A locale without the key gets the app's fallback
// language through i18next (English); the Portuguese label is the last resort,
// so a missing key never blanks a tooltip or hides an icon from search.
export function getCategoryIconLabel(t: TFunction, name: string): string {
  const key = `categoryIcons.${name}`
  const translated = t(key)
  // i18next echoes the key back when the locale has no entry for it.
  if (typeof translated === 'string' && translated && translated !== key) return translated
  return CATEGORY_ICONS.find((entry) => entry.name === name)?.pt ?? name
}

// ---------------------------------------------------------------------------
// Emoji (issue 752)
//
// A category, a category group, a goal and a workspace all store their icon as
// a free string, and the column was always wide enough for an emoji, so rows
// saved before the picker offered them still render. The picker now offers
// emoji too, which means the detection has to hold for what a phone keyboard
// or the OS emoji panel actually produces: one glyph, a skin tone, a flag, or
// a family joined by zero-width joiners. Measuring that by string length does
// not: a single emoji can be eleven code units and still be one glyph.
// ---------------------------------------------------------------------------

/** One whole emoji, from a lone pictograph to a ZWJ sequence or a flag. */
const EMOJI_SEQUENCE =
  /(?:\p{Regional_Indicator}{2}|[#*0-9]\uFE0F?\u20E3|\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?(?:[\u{E0020}-\u{E007F}]+)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?)*)/gu

function emojiSequences(text: string): string[] {
  return text.match(EMOJI_SEQUENCE) ?? []
}

/** The first emoji inside a string, or null when there is none. */
export function extractFirstEmoji(text: string): string | null {
  if (!text) return null
  return emojiSequences(text)[0] ?? null
}

/**
 * Whether a stored icon value is an emoji rather than a Lucide name.
 *
 * True when the value is made only of emoji: one glyph, several of them, a
 * flag or a ZWJ family. Whitespace between them is tolerated; any other
 * character makes it false, so "12" and "food 🍔" are not emoji.
 */
export function isEmoji(str: string): boolean {
  const value = str.trim()
  if (!value) return false
  const sequences = emojiSequences(value)
  if (sequences.length === 0) return false
  return sequences.join('') === value.replace(/\s+/g, '')
}

/**
 * The emoji equivalent of every catalogue icon, one to one.
 *
 * Same meaning, different drawing: the picker shows both and a user switching
 * to emoji keeps the same mental category. Kept flat so it is easy to read and
 * to assert that every catalogue entry has an entry here.
 */
export const LUCIDE_TO_EMOJI: Record<string, string> = {
  // Home
  house: '🏠',
  lightbulb: '💡',
  droplets: '💧',
  flame: '🔥',
  wifi: '📶',
  tv: '📺',
  refrigerator: '🧊',
  sofa: '🛋️',
  'spray-can': '🧹',
  // Food
  'utensils-crossed': '🍽️',
  coffee: '☕',
  beer: '🍺',
  pizza: '🍕',
  salad: '🥗',
  croissant: '🥐',
  cake: '🍰',
  'ice-cream-cone': '🍦',
  apple: '🍎',
  beef: '🥩',
  fish: '🐟',
  // Transport and travel
  car: '🚗',
  fuel: '⛽',
  bus: '🚌',
  train: '🚆',
  plane: '✈️',
  bike: '🚲',
  motorbike: '🏍️',
  'car-taxi-front': '🚕',
  'bed-double': '🛏️',
  luggage: '🧳',
  // Shopping
  'shopping-cart': '🛒',
  'shopping-bag': '🛍️',
  gift: '🎁',
  package: '📦',
  shirt: '👕',
  // Health
  pill: '💊',
  stethoscope: '🩺',
  syringe: '💉',
  cross: '🏥',
  heart: '❤️',
  brain: '🧠',
  dumbbell: '🏋️',
  // Leisure
  'gamepad-2': '🎮',
  music: '🎵',
  film: '🎬',
  'party-popper': '🎉',
  ticket: '🎟️',
  trophy: '🏆',
  camera: '📷',
  newspaper: '📰',
  // Technology
  smartphone: '📱',
  'credit-card': '💳',
  laptop: '💻',
  monitor: '🖥️',
  // Education
  'book-open': '📖',
  'graduation-cap': '🎓',
  school: '🏫',
  'pencil-ruler': '✏️',
  // Work
  briefcase: '💼',
  'building-2': '🏢',
  landmark: '🏦',
  // Finance
  wallet: '👛',
  banknote: '💵',
  'piggy-bank': '🐖',
  'trending-up': '📈',
  'trending-down': '📉',
  'hand-coins': '🪙',
  receipt: '🧾',
  'arrow-left-right': '🔁',
  'heart-handshake': '🤝',
  // Family and pets
  baby: '👶',
  dog: '🐶',
  cat: '🐱',
  'paw-print': '🐾',
  // Services
  scissors: '✂️',
  wrench: '🔧',
  hammer: '🔨',
  // Nature and others
  'tree-pine': '🌲',
  trees: '🌳',
  umbrella: '☂️',
  globe: '🌍',
  sparkles: '✨',
  'circle-help': '❓',
}

/** The catalogue icon an emoji stands for, for the ones that have a twin. */
export const EMOJI_TO_LUCIDE: Record<string, string> = Object.fromEntries(
  Object.entries(LUCIDE_TO_EMOJI).map(([name, emoji]) => [emoji, name])
)

export interface CategoryEmoji {
  emoji: string
  /** Lucide name this emoji is the equivalent of, when there is one. */
  icon: string | null
  /** Translation key suffix under `categoryEmojis` for an emoji with no twin. */
  key?: string
  /** Search words beyond the icon's own name and label. */
  keywords: string[]
}

// Emoji with no direct Lucide twin, kept for the everyday things the icon set
// has no drawing for. Keywords are English and Portuguese, the two languages
// the catalogue labels are written in.
const EXTRA_EMOJIS: CategoryEmoji[] = [
  { emoji: '🍔', icon: null, key: 'burger', keywords: ['burger', 'hamburger', 'lanche'] },
  { emoji: '🍟', icon: null, key: 'fries', keywords: ['fries', 'batata frita', 'fast food'] },
  { emoji: '🥪', icon: null, key: 'sandwich', keywords: ['sandwich', 'sanduíche'] },
  { emoji: '🍷', icon: null, key: 'wine', keywords: ['wine', 'vinho'] },
  { emoji: '🍸', icon: null, key: 'cocktail', keywords: ['cocktail', 'drink', 'drinque'] },
  { emoji: '🧃', icon: null, key: 'juice', keywords: ['juice', 'suco'] },
  { emoji: '💰', icon: null, key: 'money-bag', keywords: ['money bag', 'savings', 'dinheiro', 'poupança'] },
  { emoji: '💸', icon: null, key: 'spending', keywords: ['spending', 'expense', 'gasto', 'despesa'] },
  { emoji: '⚽', icon: null, key: 'soccer', keywords: ['soccer', 'football', 'futebol'] },
  { emoji: '🏀', icon: null, key: 'basketball', keywords: ['basketball', 'basquete'] },
  { emoji: '🎾', icon: null, key: 'tennis', keywords: ['tennis', 'tênis'] },
  { emoji: '🧘', icon: null, key: 'yoga', keywords: ['yoga', 'meditation', 'meditação'] },
  { emoji: '🎨', icon: null, key: 'art', keywords: ['art', 'hobby', 'arte'] },
  { emoji: '🎧', icon: null, key: 'headphones', keywords: ['headphones', 'fone de ouvido'] },
  { emoji: '🔌', icon: null, key: 'plug', keywords: ['plug', 'charger', 'tomada', 'carregador'] },
  { emoji: '🛠️', icon: null, key: 'tools', keywords: ['tools', 'ferramentas'] },
  { emoji: '🧼', icon: null, key: 'soap', keywords: ['soap', 'sabonete', 'higiene'] },
  { emoji: '🚿', icon: null, key: 'shower', keywords: ['shower', 'banho'] },
  { emoji: '👟', icon: null, key: 'shoes', keywords: ['shoes', 'sneakers', 'calçado'] },
  { emoji: '💄', icon: null, key: 'makeup', keywords: ['makeup', 'maquiagem', 'beleza'] },
  { emoji: '🧸', icon: null, key: 'toys', keywords: ['toys', 'brinquedos', 'criança'] },
  { emoji: '🌷', icon: null, key: 'flowers', keywords: ['flowers', 'flores', 'jardim'] },
  { emoji: '🍀', icon: null, key: 'luck', keywords: ['luck', 'sorte'] },
  { emoji: '🎂', icon: null, key: 'birthday', keywords: ['birthday', 'aniversário'] },
  { emoji: '🎄', icon: null, key: 'christmas', keywords: ['christmas', 'natal'] },
  { emoji: '🛡️', icon: null, key: 'protection', keywords: ['insurance', 'protection', 'seguro', 'proteção'] },
  { emoji: '🗺️', icon: null, key: 'map', keywords: ['map', 'travel', 'mapa', 'viagem'] },
  { emoji: '🏖️', icon: null, key: 'beach', keywords: ['beach', 'holiday', 'praia', 'férias'] },
  { emoji: '🚀', icon: null, key: 'launch', keywords: ['launch', 'startup', 'lançamento'] },
  { emoji: '💎', icon: null, key: 'investment', keywords: ['investment', 'luxury', 'investimento', 'luxo'] },
  { emoji: '🏗️', icon: null, key: 'construction', keywords: ['construction', 'obra', 'construção'] },
]

// An emoji's name follows the active language, the same way an icon's does.
// The extras have no icon to borrow a label from, so each carries its own
// translation key; the first keyword is the fallback and the glyph itself the
// last resort, so a locale missing the key never blanks the tooltip.
export function getCategoryEmojiLabel(t: TFunction, entry: CategoryEmoji): string {
  if (entry.icon) return getCategoryIconLabel(t, entry.icon)
  if (entry.key) {
    const key = `categoryEmojis.${entry.key}`
    const translated = t(key)
    if (typeof translated === 'string' && translated && translated !== key) return translated
  }
  return entry.keywords[0] ?? entry.emoji
}

/** Every emoji the picker offers: one per catalogue icon, plus the extras. */
export const CATEGORY_EMOJIS: CategoryEmoji[] = [
  ...CATEGORY_ICONS.map((entry) => ({
    emoji: LUCIDE_TO_EMOJI[entry.name],
    icon: entry.name,
    keywords: [] as string[],
  })),
  ...EXTRA_EMOJIS,
]
