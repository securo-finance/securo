// frontend/src/components/page-header.tsx
interface PageHeaderProps {
  section: string
  title: string
  action?: React.ReactNode
}

export function PageHeader({ section, title, action }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between mb-6">
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-0.5">{section}</p>
        <h1 className="text-2xl font-semibold text-foreground tracking-tight">{title}</h1>
      </div>
      {action}
    </div>
  )
}
