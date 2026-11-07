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

// Check if a string is an emoji (for backward compatibility with old data)
export function isEmoji(str: string): boolean {
  // Emoji strings are typically 1-2 characters with high code points
  return str.length <= 2 && /\p{Emoji}/u.test(str)
}
