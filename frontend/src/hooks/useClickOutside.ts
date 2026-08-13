import { useEffect, type RefObject } from "react";

export function useClickOutside(ref: RefObject<HTMLElement>, onOutside: () => void): void {
  useEffect(() => {
    function handler(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onOutside();
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [ref, onOutside]);
}
