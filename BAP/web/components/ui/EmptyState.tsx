import type { ReactNode } from 'react';

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <h2 className="text-base font-semibold text-neutral-900 sm:text-lg">{title}</h2>
      {description && <p className="text-sm text-neutral-600 sm:text-base">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
