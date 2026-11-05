import type { JSX, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/** Een lege lijst of een pagina zonder inhoud: een icoon, een kop, uitleg en eventueel een knop. */
export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: IconName;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}): JSX.Element {
  return (
    <div className="empty-state">
      {icon && (
        <span className="empty-state-icon">
          <Icon name={icon} size={32} />
        </span>
      )}
      <h2>{title}</h2>
      {children && <div className="muted">{children}</div>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}
