import { type ButtonHTMLAttributes, forwardRef } from "react";
import { Icon, type IconName } from "./Icon";
import { Spinner } from "./Spinner";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "ghost" | "link";
  icon?: IconName;
  /** Bezig: de knop doet niets en toont een draaiertje (aria-busy). */
  busy?: boolean;
};

/** Een knop; type="button" tenzij anders gezegd (nooit per ongeluk een formulier versturen). */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "default", icon, busy = false, className, children, type = "button", disabled, onClick, ...rest },
  ref,
) {
  const classes = ["btn", variant !== "default" ? variant : null, className].filter(Boolean).join(" ");
  return (
    <button
      ref={ref}
      type={type}
      className={classes}
      disabled={disabled}
      aria-busy={busy || undefined}
      onClick={busy ? (e) => e.preventDefault() : onClick}
      {...rest}
    >
      {busy ? <Spinner inline /> : icon ? <Icon name={icon} size={16} /> : null}
      {children}
    </button>
  );
});

export type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: IconName;
  /** Wat de knop doet: aria-label en tooltip (title) */
  label: string;
  /** Een aan/uit-knop (aria-pressed) */
  pressed?: boolean;
};

/** Een knop met alleen een icoon. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, pressed, className, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={["icon-button", className].filter(Boolean).join(" ")}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      {...rest}
    >
      <Icon name={icon} size={20} />
    </button>
  );
});
