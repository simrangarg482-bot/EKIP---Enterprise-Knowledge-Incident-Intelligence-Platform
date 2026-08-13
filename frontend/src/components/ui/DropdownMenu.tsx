import { useRef, useState, type ReactNode } from "react";
import { useClickOutside } from "@/hooks/useClickOutside";
import { cn } from "@/utils/cn";

interface DropdownItem {
  label: string;
  onSelect: () => void;
  destructive?: boolean;
}

interface DropdownMenuProps {
  trigger: ReactNode;
  items: DropdownItem[];
  align?: "left" | "right";
}

export function DropdownMenu({ trigger, items, align = "right" }: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useClickOutside(ref, () => setOpen(false));

  return (
    <div className="relative inline-block" ref={ref}>
      <div onClick={() => setOpen((o) => !o)}>{trigger}</div>
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute z-40 mt-1 min-w-[10rem] rounded-md border border-border bg-white py-1 shadow-panel",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {items.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              onClick={() => {
                item.onSelect();
                setOpen(false);
              }}
              className={cn(
                "block w-full px-3 py-1.5 text-left text-sm hover:bg-slate-50",
                item.destructive ? "text-critical" : "text-ink",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
