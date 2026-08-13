import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/utils/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-md border border-border bg-white px-3 text-sm text-ink placeholder:text-ink-subtle",
        "focus-visible:border-accent",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
