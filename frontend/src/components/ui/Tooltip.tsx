import { useState, type ReactNode } from "react";
import { cn } from "@/utils/cn";

interface TooltipProps {
  content: string;
  children: ReactNode;
  side?: "top" | "bottom" | "right";
}

export function Tooltip({ content, children, side = "top" }: TooltipProps) {
  const [visible, setVisible] = useState(false);

  const positionClasses: Record<typeof side, string> = {
    top: "bottom-full left-1/2 -translate-x-1/2 mb-1.5",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-1.5",
    right: "left-full top-1/2 -translate-y-1/2 ml-1.5",
  };

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >
      {children}
      {visible && (
        <span
          role="tooltip"
          className={cn(
            "absolute z-50 whitespace-nowrap rounded-md bg-sidebar px-2 py-1 text-xs text-white shadow-panel",
            positionClasses[side],
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}
