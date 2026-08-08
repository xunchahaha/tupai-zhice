import { type ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "icon";

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }
>(({ className, variant = "primary", size = "md", type = "button", ...props }, ref) => (
  <button
    ref={ref}
    type={type}
    className={cn(
      "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
      "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600",
      {
        primary: "bg-zinc-900 text-white hover:bg-zinc-700",
        secondary: "bg-zinc-100 text-zinc-800 hover:bg-zinc-200",
        ghost: "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-950",
        danger: "bg-red-600 text-white hover:bg-red-700",
        outline: "border border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-50",
      }[variant],
      { sm: "h-8 px-2.5 text-xs", md: "h-9 px-3 text-sm", icon: "size-8 p-0" }[size],
      className,
    )}
    {...props}
  />
));
Button.displayName = "Button";
