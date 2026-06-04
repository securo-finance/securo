import type { LucideIcon } from 'lucide-react'
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
  PartyPopper, TreePine, Umbrella, Globe, Sparkles
  , Camera, Headphones, Monitor, Laptop, PawPrint, Leaf, Cannabis
} from 'lucide-react'

export interface CategoryIconEntry {
  name: string      // Lucide icon name (stored in DB)
  label: string     // Portuguese label for search
  icon: LucideIcon  // Component reference
}

export const CATEGORY_ICONS: CategoryIconEntry[] = [
  // Moradia & Casa
  { name: 'house', label: 'Casa / Moradia', icon: House },
  { name: 'lightbulb', label: 'Luz / Energia', icon: Lightbulb },
  { name: 'droplets', label: 'Água', icon: Droplets },
  { name: 'flame', label: 'Gás', icon: Flame },
  { name: 'wifi', label: 'Internet / Wi-Fi', icon: Wifi },
  { name: 'tv', label: 'TV / Streaming', icon: Tv },
  // Alimentação
  { name: 'utensils-crossed', label: 'Alimentação / Restaurante', icon: UtensilsCrossed },
  { name: 'coffee', label: 'Café', icon: Coffee },
  { name: 'beer', label: 'Bebidas / Bar', icon: Beer },
  { name: 'pizza', label: 'Pizza / Fast Food', icon: Pizza },
  { name: 'salad', label: 'Salada / Saudável', icon: Salad },
  // Transporte
  { name: 'car', label: 'Carro / Transporte', icon: Car },
  { name: 'fuel', label: 'Combustível / Gasolina', icon: Fuel },
  { name: 'bus', label: 'Ônibus', icon: Bus },
  { name: 'train', label: 'Trem / Metrô', icon: Train },
  { name: 'plane', label: 'Avião / Viagem', icon: Plane },
  { name: 'bike', label: 'Bicicleta', icon: Bike },
  // Compras
  { name: 'shopping-cart', label: 'Mercado / Supermercado', icon: ShoppingCart },
  { name: 'shopping-bag', label: 'Compras / Loja', icon: ShoppingBag },
  { name: 'gift', label: 'Presente', icon: Gift },
  { name: 'shirt', label: 'Roupa / Vestuário', icon: Shirt },
  // Saúde
  { name: 'pill', label: 'Remédio / Farmácia', icon: Pill },
  { name: 'stethoscope', label: 'Médico / Consulta', icon: Stethoscope },
  { name: 'syringe', label: 'Vacina / Exame', icon: Syringe },
  { name: 'cross', label: 'Hospital / Emergência', icon: Cross },
  { name: 'heart', label: 'Saúde / Bem-estar', icon: Heart },
  { name: 'dumbbell', label: 'Academia / Exercício', icon: Dumbbell },
  // Lazer
  { name: 'gamepad-2', label: 'Jogos / Lazer', icon: Gamepad2 },
  { name: 'music', label: 'Música', icon: Music },
  { name: 'film', label: 'Cinema / Filme', icon: Film },
  { name: 'party-popper', label: 'Festa / Evento', icon: PartyPopper },
  { name: 'gamepad', label: 'Games / Videogame', icon: Gamepad2 },
  { name: 'headphones', label: 'Fones / Áudio', icon: Headphones },
  { name: 'camera', label: 'Câmera / Foto', icon: Camera },
  // Tecnologia & Assinaturas
  { name: 'smartphone', label: 'Celular / Telefone', icon: Smartphone },
  { name: 'monitor', label: 'Monitor / Tela', icon: Monitor },
  { name: 'laptop', label: 'Notebook / Laptop', icon: Laptop },
  { name: 'credit-card', label: 'Cartão de Crédito', icon: CreditCard },
  // Educação
  { name: 'book-open', label: 'Livro / Leitura', icon: BookOpen },
  { name: 'graduation-cap', label: 'Educação / Curso', icon: GraduationCap },
  // Trabalho & Negócios
  { name: 'briefcase', label: 'Trabalho / Negócios', icon: Briefcase },
  { name: 'building-2', label: 'Empresa / Escritório', icon: Building2 },
  { name: 'landmark', label: 'Banco / Governo', icon: Landmark },
  // Finanças
  { name: 'wallet', label: 'Carteira', icon: Wallet },
  { name: 'banknote', label: 'Dinheiro', icon: Banknote },
  { name: 'piggy-bank', label: 'Poupança / Investimento', icon: PiggyBank },
  { name: 'trending-up', label: 'Rendimento / Lucro', icon: TrendingUp },
  { name: 'trending-down', label: 'Prejuízo / Perda', icon: TrendingDown },
  { name: 'receipt', label: 'Recibo / Nota Fiscal', icon: Receipt },
  { name: 'arrow-left-right', label: 'Transferência', icon: ArrowLeftRight },
  // Família & Pets
  { name: 'baby', label: 'Bebê / Criança', icon: Baby },
  { name: 'dog', label: 'Pet / Cachorro', icon: Dog },
  { name: 'cat', label: 'Pet / Gato', icon: Cat },
  { name: 'paw', label: 'Pet / Acessórios', icon: PawPrint },
  // Serviços & Manutenção
  { name: 'scissors', label: 'Cabelo / Beleza', icon: Scissors },
  { name: 'wrench', label: 'Manutenção / Reparo', icon: Wrench },
  { name: 'hammer', label: 'Construção / Reforma', icon: Hammer },
  // Outros
  { name: 'tree-pine', label: 'Natureza / Jardim', icon: TreePine },
  { name: 'umbrella', label: 'Seguro', icon: Umbrella },
  { name: 'globe', label: 'Internacional', icon: Globe },
  { name: 'sparkles', label: 'Especial', icon: Sparkles },
  { name: 'leaf', label: 'planta ', icon: Leaf },
  { name: 'cannabis', label: 'cannabis / maconha', icon: Cannabis },
  { name: 'circle-help', label: 'Outros / Indefinido', icon: CircleHelp },
]

// O(1) lookup map: icon-name → LucideIcon component
export const ICON_MAP: Record<string, LucideIcon> = Object.fromEntries(
  CATEGORY_ICONS.map((entry) => [entry.name, entry.icon])
)

// Check if a string is an emoji (for backward compatibility with old data)
export function isEmoji(str: string): boolean {
  // Emoji strings are typically 1-2 characters with high code points
  return str.length <= 2 && /\p{Emoji}/u.test(str)
}
